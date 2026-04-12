"""Domain service: HybridSearchService.

Orquestra busca híbrida (FTS + semântica) com Reciprocal Rank Fusion (RRF).

Estratégia:
1. Busca paralela: FTS (search_pages + search_chunks) + semântica (search_semantic)
2. Merge via RRF (k=60): combina rankings de múltiplas estratégias
3. Query expansion condicional: se < 3 resultados bons, expande via dicionário local
4. Degradação graciosa: se EmbeddingGateway indisponível, usa apenas FTS

Implementa SearchRepository — pode substituir PostgresSearchRepository diretamente
no wiring do FastAPI app sem alterar ChatService.
"""

from __future__ import annotations

import asyncio
import logging

from app.domain.entities.document_table import DocumentTable
from app.domain.ports.outbound.embedding_gateway import (
    EmbeddingGateway,
    EmbeddingUnavailableError,
)
from app.domain.ports.outbound.search_repository import SearchRepository
from app.domain.value_objects.search_result import ChunkMatch, PageMatch, SemanticMatch

logger = logging.getLogger(__name__)

# ─── Parâmetros de RRF ────────────────────────────────────────────────────────

_RRF_K = 60       # constante padrão de Reciprocal Rank Fusion
_MIN_GOOD_RESULTS = 3   # threshold para acionar query expansion

# ─── Dicionário local de sinônimos para query expansion ───────────────────────

_SYNONYMS: dict[str, list[str]] = {
    "diárias": ["ajuda de custo", "indenização de viagem", "diárias pagas"],
    "diarias": ["ajuda de custo", "indenização de viagem", "diárias"],
    "viagens": ["deslocamento", "missão oficial", "diárias"],
    "licitações": ["pregão", "concorrência", "dispensa", "contratação"],
    "licitacoes": ["pregão", "concorrência", "dispensa", "contratação"],
    "contratos": ["ajuste", "acordo", "contratação", "convênio"],
    "servidores": ["funcionários", "colaboradores", "quadro de pessoal"],
    "salários": ["remuneração", "vencimentos", "folha de pagamento"],
    "salarios": ["remuneração", "vencimentos", "folha de pagamento"],
    "orçamento": ["dotação", "receita", "despesa", "LOA"],
    "orcamento": ["dotação", "receita", "despesa", "LOA"],
    "compras": ["aquisições", "licitações", "pregão eletrônico"],
    "gastos": ["despesas", "valores pagos", "custos"],
}


def _expand_query(query: str) -> str | None:
    """Retorna query expandida com sinônimos, ou None se nenhum sinônimo encontrado."""
    words = query.lower().split()
    extras: list[str] = []
    for word in words:
        if word in _SYNONYMS:
            extras.extend(_SYNONYMS[word])
    if not extras:
        return None
    return query + " " + " ".join(extras)


# ─── RRF ──────────────────────────────────────────────────────────────────────


def _rrf_score(rank: int, k: int = _RRF_K) -> float:
    return 1.0 / (k + rank + 1)


def _merge_chunk_results_rrf(
    fts_chunks: list[ChunkMatch],
    semantic_chunks: list[SemanticMatch],
    top_k: int,
) -> list[ChunkMatch]:
    """Merge de ChunkMatch (FTS) + SemanticMatch → ChunkMatch via RRF.

    Deduplica por chunk_id: um chunk que aparece em ambas as listas recebe
    score combinado (soma das pontuações RRF de cada estratégia).
    """
    scores: dict[int, float] = {}

    # Contribuição FTS
    for rank, match in enumerate(fts_chunks):
        cid = match.chunk.id
        scores[cid] = scores.get(cid, 0.0) + _rrf_score(rank)

    # Contribuição semântica
    for rank, match in enumerate(semantic_chunks):
        cid = match.source_id
        scores[cid] = scores.get(cid, 0.0) + _rrf_score(rank)

    # Ordena por score RRF decrescente
    sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)

    # Reconstrói ChunkMatch, priorizando os objetos FTS (têm document_title preenchido)
    fts_by_id = {m.chunk.id: m for m in fts_chunks}
    sem_by_id = {m.source_id: m for m in semantic_chunks}

    result: list[ChunkMatch] = []
    for cid in sorted_ids[:top_k]:
        if cid in fts_by_id:
            cm = fts_by_id[cid]
            result.append(ChunkMatch(
                chunk=cm.chunk,
                document_title=cm.document_title,
                document_url=cm.document_url,
                score=scores[cid],
            ))
        elif cid in sem_by_id:
            sm = sem_by_id[cid]
            result.append(ChunkMatch(
                chunk=sm.chunk,  # type: ignore[arg-type]
                document_title=sm.document_title or "",
                document_url=sm.document_url or "",
                score=scores[cid],
            ))

    return result


def _merge_page_results_rrf(
    fts_pages: list[PageMatch],
    semantic_pages: list[SemanticMatch],
    top_k: int,
) -> list[PageMatch]:
    """Merge de PageMatch (FTS) + SemanticMatch → PageMatch via RRF."""
    scores: dict[int, float] = {}

    for rank, match in enumerate(fts_pages):
        pid = match.page.id
        scores[pid] = scores.get(pid, 0.0) + _rrf_score(rank)

    for rank, match in enumerate(semantic_pages):
        pid = match.source_id
        scores[pid] = scores.get(pid, 0.0) + _rrf_score(rank)

    sorted_ids = sorted(scores.keys(), key=lambda pid: scores[pid], reverse=True)

    fts_by_id = {m.page.id: m for m in fts_pages}
    sem_by_id = {m.source_id: m for m in semantic_pages}

    result: list[PageMatch] = []
    for pid in sorted_ids[:top_k]:
        if pid in fts_by_id:
            pm = fts_by_id[pid]
            result.append(PageMatch(page=pm.page, score=scores[pid], highlight=pm.highlight))
        elif pid in sem_by_id:
            sm = sem_by_id[pid]
            result.append(PageMatch(page=sm.page, score=scores[pid]))  # type: ignore[arg-type]

    return result


# ─── HybridSearchService ──────────────────────────────────────────────────────


class HybridSearchService(SearchRepository):
    """Wrapper do SearchRepository que adiciona busca semântica + RRF.

    Usa o mesmo SearchRepository internamente para FTS e search_semantic.
    EmbeddingGateway é opcional — sem ele, funciona como FTS puro.
    """

    def __init__(
        self,
        search_repo: SearchRepository,
        embedding_gateway: EmbeddingGateway | None = None,
        top_k: int = 5,
    ) -> None:
        self._repo = search_repo
        self._embedding_gw = embedding_gateway
        self._top_k = top_k

    # ── Busca de pages (híbrida) ──────────────────────────────────────────────

    async def search_pages(self, query: str, top_k: int = 5) -> list[PageMatch]:
        effective_top_k = top_k or self._top_k
        fts_task = asyncio.create_task(self._repo.search_pages(query, effective_top_k))
        sem_task = asyncio.create_task(self._semantic_pages(query, effective_top_k))

        fts_pages, sem_pages = await asyncio.gather(fts_task, sem_task)
        merged = _merge_page_results_rrf(fts_pages, sem_pages, effective_top_k)

        # Query expansion se poucos resultados
        if len(merged) < _MIN_GOOD_RESULTS:
            merged = await self._expand_and_search_pages(query, effective_top_k, merged)

        return merged

    # ── Busca de chunks (híbrida) ─────────────────────────────────────────────

    async def search_chunks(self, query: str, top_k: int = 5) -> list[ChunkMatch]:
        effective_top_k = top_k or self._top_k
        fts_task = asyncio.create_task(self._repo.search_chunks(query, effective_top_k))
        sem_task = asyncio.create_task(self._semantic_chunks(query, effective_top_k))

        fts_chunks, sem_chunks = await asyncio.gather(fts_task, sem_task)
        merged = _merge_chunk_results_rrf(fts_chunks, sem_chunks, effective_top_k)

        # Query expansion se poucos resultados
        if len(merged) < _MIN_GOOD_RESULTS:
            merged = await self._expand_and_search_chunks(query, effective_top_k, merged)

        return merged

    # ── Busca de tabelas (delega ao repo — tabelas não têm RRF por ora) ───────

    async def search_tables(self, query: str) -> list[DocumentTable]:
        return await self._repo.search_tables(query)

    # ── search_semantic (delega) ──────────────────────────────────────────────

    async def search_semantic(
        self,
        query_embedding: list[float],
        source_type: str = "chunk",
        top_k: int = 5,
        filters: dict[str, object] | None = None,
    ) -> list[SemanticMatch]:
        return await self._repo.search_semantic(query_embedding, source_type, top_k, filters)

    # ── Métodos de suporte ────────────────────────────────────────────────────

    async def get_categories(self) -> list[dict[str, object]]:
        return await self._repo.get_categories()

    async def get_stats(self) -> dict[str, object]:
        return await self._repo.get_stats()

    # ── Helpers internos ──────────────────────────────────────────────────────

    async def _semantic_chunks(self, query: str, top_k: int) -> list[SemanticMatch]:
        """Tenta busca semântica de chunks + page_chunks + synthetic_queries.

        Retorna [] se gateway indisponível.
        """
        if self._embedding_gw is None:
            return []
        try:
            embedding = await self._embedding_gw.embed_text(query, task_type="RETRIEVAL_QUERY")
            # Busca em paralelo: document_chunks, page_chunks e via perguntas sintéticas
            doc_chunks, page_chunks, sq_chunks = await asyncio.gather(
                self._repo.search_semantic(embedding, source_type="chunk", top_k=top_k),
                self._repo.search_semantic(embedding, source_type="page_chunk", top_k=top_k),
                self._repo.search_semantic(embedding, source_type="synthetic_query", top_k=top_k),
            )
            return doc_chunks + page_chunks + sq_chunks
        except EmbeddingUnavailableError:
            logger.info("HybridSearch: EmbeddingGateway indisponível — usando apenas FTS para chunks.")
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("HybridSearch: erro na busca semântica de chunks: %s", exc)
            return []

    async def _semantic_pages(self, query: str, top_k: int) -> list[SemanticMatch]:
        """Tenta busca semântica de pages; retorna [] se gateway indisponível."""
        if self._embedding_gw is None:
            return []
        try:
            embedding = await self._embedding_gw.embed_text(query, task_type="RETRIEVAL_QUERY")
            return await self._repo.search_semantic(embedding, source_type="page", top_k=top_k)
        except EmbeddingUnavailableError:
            logger.info("HybridSearch: EmbeddingGateway indisponível — usando apenas FTS para pages.")
            return []
        except Exception as exc:  # noqa: BLE001
            logger.warning("HybridSearch: erro na busca semântica de pages: %s", exc)
            return []

    async def _expand_and_search_chunks(
        self,
        query: str,
        top_k: int,
        current_results: list[ChunkMatch],
    ) -> list[ChunkMatch]:
        """Aplica query expansion se há poucos resultados."""
        expanded = _expand_query(query)
        if expanded is None or expanded == query:
            return current_results
        logger.info("HybridSearch: query expansion '%s' → '%s'", query[:50], expanded[:80])
        try:
            extra_fts = await self._repo.search_chunks(expanded, top_k)
            extra_sem = await self._semantic_chunks(expanded, top_k)
            # Regressa com os resultados atuais mais os extras, re-rankeia com RRF
            combined_fts = current_results + [
                m for m in extra_fts if m.chunk.id not in {c.chunk.id for c in current_results}
            ]
            combined_sem = extra_sem
            return _merge_chunk_results_rrf(combined_fts, combined_sem, top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HybridSearch: query expansion falhou: %s", exc)
            return current_results

    async def _expand_and_search_pages(
        self,
        query: str,
        top_k: int,
        current_results: list[PageMatch],
    ) -> list[PageMatch]:
        """Aplica query expansion para pages se há poucos resultados."""
        expanded = _expand_query(query)
        if expanded is None or expanded == query:
            return current_results
        try:
            extra_fts = await self._repo.search_pages(expanded, top_k)
            extra_sem = await self._semantic_pages(expanded, top_k)
            combined_fts = current_results + [
                m for m in extra_fts if m.page.id not in {p.page.id for p in current_results}
            ]
            return _merge_page_results_rrf(combined_fts, extra_sem, top_k)
        except Exception as exc:  # noqa: BLE001
            logger.warning("HybridSearch: page expansion falhou: %s", exc)
            return current_results
