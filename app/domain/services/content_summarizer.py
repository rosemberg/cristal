"""Domain service: ContentSummarizerService.

Gera sumários em linguagem natural para páginas e seções de documentos,
persistindo-os em `pages.content_summary` / `section_summaries` e seus
embeddings em `embeddings` com source_type='page_summary' ou 'section_summary'.

Estratégia (Fase 2 — Sumarização e Indexação Multinível):
1. Consulta páginas sem embedding page_summary (indicador de sumário LLM)
2. Envia ao LLM em batches de 10-20 páginas por chamada
3. Atualiza pages.content_summary e gera embeddings com source_type='page_summary'
4. Para documentos longos (>10 páginas): gera sumários por seção e persiste
   em section_summaries com embeddings source_type='section_summary'

Fallback:
- JSON malformado → tenta regex antes de descartar o batch
- Erros LLM → registra erro, continua para próximo batch
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from app.domain.ports.outbound.embedding_gateway import EmbeddingGateway
from app.domain.ports.outbound.embedding_repository import EmbeddingRecord, EmbeddingRepository
from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.value_objects.content_summary import (
    PageSummary,
    SectionSummary,
    SummarizationResult,
)

logger = logging.getLogger(__name__)

# ─── Prompts — Sumários de Páginas ────────────────────────────────────────────

_PAGE_SYSTEM_PROMPT = (
    "Você é um especialista em transparência pública brasileira. "
    "Sua tarefa é resumir conteúdos de páginas do portal de transparência do "
    "TRE-PI (Tribunal Regional Eleitoral do Piauí). "
    "Responda APENAS com JSON válido, sem texto adicional."
)

_PAGE_USER_TEMPLATE = """\
Para cada página abaixo, gere um resumo de 2 a 3 frases objetivas em português \
brasileiro. Foque em: o que a página contém, período de referência, e tipo de \
informação (financeira, administrativa, licitação, etc.).

Retorne JSON no formato: [{{"page_id": N, "summary": "..."}}]

Páginas:
{pages_json}
"""

# ─── Prompts — Sumários de Seções ─────────────────────────────────────────────

_SECTION_SYSTEM_PROMPT = (
    "Você é um especialista em transparência pública brasileira. "
    "Divida o documento em seções lógicas e gere um resumo para cada seção. "
    "Responda APENAS com JSON válido, sem texto adicional."
)

_SECTION_USER_TEMPLATE = """\
Analise o documento abaixo e divida-o em seções lógicas (3 a 6 seções). \
Para cada seção, gere um resumo de 2 a 3 frases objetivas.

Título do documento: {title}
Número de páginas: {num_pages}

Conteúdo:
{content}

Retorne JSON no formato:
[{{"section_title": "...", "page_range": "1-5", "summary": "..."}}]
"""

# ─── Regex fallbacks ──────────────────────────────────────────────────────────

_PAGE_JSON_RE = re.compile(
    r'\{[^{}]*"page_id"\s*:\s*(\d+)[^{}]*"summary"\s*:\s*"([^"]+)"'
)
_SECTION_JSON_RE = re.compile(
    r'\{[^{}]*"section_title"\s*:\s*"([^"]*)"[^{}]*"summary"\s*:\s*"([^"]+)"'
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _parse_json_response(raw: str) -> list[dict]:
    """Tenta parsear resposta JSON do LLM. Fallback para regex."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed  # type: ignore[return-value]
    except json.JSONDecodeError:
        pass
    return []


def _parse_page_summaries(raw: str, page_ids: list[int]) -> dict[int, str]:
    """Parseia resposta e retorna {page_id: summary}."""
    items = _parse_json_response(raw)
    result: dict[int, str] = {}

    if items:
        for item in items:
            if isinstance(item.get("page_id"), int) and isinstance(item.get("summary"), str):
                result[item["page_id"]] = item["summary"].strip()
        if result:
            return result

    # Regex fallback
    for m in _PAGE_JSON_RE.finditer(raw):
        try:
            pid = int(m.group(1))
            summary = m.group(2).strip()
            if pid in page_ids and summary:
                result[pid] = summary
        except (ValueError, IndexError):
            continue

    return result


def _parse_section_summaries(raw: str) -> list[dict]:
    """Parseia resposta de seções. Retorna lista de {section_title, page_range, summary}."""
    items = _parse_json_response(raw)
    if items:
        valid = [
            i for i in items
            if isinstance(i.get("summary"), str) and i["summary"].strip()
        ]
        if valid:
            return valid

    # Regex fallback
    results = []
    for m in _SECTION_JSON_RE.finditer(raw):
        results.append({
            "section_title": m.group(1).strip() or None,
            "summary": m.group(2).strip(),
        })
    return results


def _build_pages_json(pages: list[dict]) -> str:
    """Serializa lista de páginas para o prompt."""
    return json.dumps(
        [
            {
                "page_id": p["id"],
                "title": p["title"],
                "category": p.get("category") or "",
                "subcategory": p.get("subcategory") or "",
                "content": (p.get("main_content") or "")[:3000],
            }
            for p in pages
        ],
        ensure_ascii=False,
        indent=2,
    )


# ─── Service ──────────────────────────────────────────────────────────────────


class ContentSummarizerService:
    """Orquestra a geração de sumários de páginas e seções de documentos."""

    def __init__(
        self,
        llm_gateway: LLMGateway,
        embedding_gateway: EmbeddingGateway,
        embedding_repo: EmbeddingRepository,
        model_name: str = "gemini-2.5-flash-lite",
        llm_batch_size: int = 10,
        section_min_pages: int = 10,
    ) -> None:
        self._llm = llm_gateway
        self._embedding_gw = embedding_gateway
        self._emb_repo = embedding_repo
        self._model_name = model_name
        self._llm_batch_size = llm_batch_size
        self._section_min_pages = section_min_pages

    # ── generate_for_pending_pages ────────────────────────────────────────────

    async def generate_for_pending_pages(
        self, batch_size: int = 100
    ) -> SummarizationResult:
        """Gera sumários LLM para páginas que ainda não têm embedding page_summary."""
        covered = await self._get_covered_page_ids()
        pending = await self._fetch_pending_pages_from_db(covered, batch_size)

        if not pending:
            logger.info("ContentSummarizer: nenhuma página pendente.")
            return SummarizationResult(skipped=len(covered))

        result = SummarizationResult(skipped=len(covered))
        logger.info("ContentSummarizer: %d páginas pendentes.", len(pending))

        for i in range(0, len(pending), self._llm_batch_size):
            sub = pending[i : i + self._llm_batch_size]
            try:
                summaries = await self._call_llm_for_pages(sub)
                await self._update_page_summaries(summaries)
                emb_count = await self._generate_page_embeddings(summaries)

                result.pages_processed += len(sub)
                result.summaries_generated += len(summaries)
                result.embeddings_created += emb_count
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ContentSummarizer: erro no batch de páginas [%d:%d]: %s",
                    i, i + len(sub), exc,
                )
                result.errors += len(sub)

        return result

    # ── generate_section_summaries ────────────────────────────────────────────

    async def generate_section_summaries_for_pending_docs(
        self, batch_size: int = 20
    ) -> SummarizationResult:
        """Gera sumários de seções para documentos longos ainda não processados."""
        covered_doc_ids = await self._get_covered_document_ids()
        pending_docs = await self._fetch_pending_documents_from_db(
            covered_doc_ids, batch_size
        )

        if not pending_docs:
            logger.info("ContentSummarizer: nenhum documento longo pendente.")
            return SummarizationResult(skipped=len(covered_doc_ids))

        result = SummarizationResult(skipped=len(covered_doc_ids))
        logger.info(
            "ContentSummarizer: %d documentos pendentes para sumário de seções.",
            len(pending_docs),
        )

        for doc in pending_docs:
            try:
                sections_data = await self._call_llm_for_sections(doc)
                if not sections_data:
                    result.errors += 1
                    continue

                section_ids = await self._save_section_summaries(doc["id"], sections_data)
                emb_count = await self._generate_section_embeddings(
                    sections_data, section_ids
                )
                result.pages_processed += 1
                result.summaries_generated += len(section_ids)
                result.embeddings_created += emb_count
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "ContentSummarizer: erro ao sumarizar documento %d: %s",
                    doc["id"], exc,
                )
                result.errors += 1

        return result

    # ── regenerate_for_page ───────────────────────────────────────────────────

    async def regenerate_for_page(self, page_id: int) -> int:
        """Remove embedding existente e regenera sumário para uma página específica."""
        await self._emb_repo.delete_by_source(page_id, "page_summary")

        page = await self._fetch_single_page_from_db(page_id)
        if page is None:
            logger.warning("ContentSummarizer: página %d não encontrada.", page_id)
            return 0

        summaries = await self._call_llm_for_pages([page])
        if not summaries:
            return 0

        await self._update_page_summaries(summaries)
        return await self._generate_page_embeddings(summaries)

    # ── get_status ────────────────────────────────────────────────────────────

    async def get_status(self) -> dict[str, object]:
        """Retorna status atual da sumarização."""
        covered_pages = await self._get_covered_page_ids()
        covered_docs = await self._get_covered_document_ids()
        total_pages = await self._count_total_pages()
        total_long_docs = await self._count_long_documents()

        return {
            "pages_with_summary_embedding": len(covered_pages),
            "total_pages": total_pages,
            "pages_pending": max(0, total_pages - len(covered_pages)),
            "documents_with_section_summaries": len(covered_docs),
            "total_long_documents": total_long_docs,
            "documents_pending": max(0, total_long_docs - len(covered_docs)),
        }

    # ── LLM helpers ───────────────────────────────────────────────────────────

    async def _call_llm_for_pages(self, pages: list[dict]) -> dict[int, str]:
        """Chama LLM e retorna {page_id: summary_text}."""
        page_ids = [p["id"] for p in pages]
        prompt = _PAGE_USER_TEMPLATE.format(pages_json=_build_pages_json(pages))
        raw = await self._llm.generate(
            system_prompt=_PAGE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return _parse_page_summaries(raw, page_ids)

    async def _call_llm_for_sections(self, doc: dict) -> list[dict]:
        """Chama LLM e retorna lista de {section_title, page_range, summary}."""
        content = (doc.get("full_text") or "")[:12000]
        prompt = _SECTION_USER_TEMPLATE.format(
            title=doc.get("document_title") or doc.get("document_url", ""),
            num_pages=doc.get("num_pages", "?"),
            content=content,
        )
        raw = await self._llm.generate(
            system_prompt=_SECTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
        )
        return _parse_section_summaries(raw)

    # ── Embedding helpers ─────────────────────────────────────────────────────

    async def _generate_page_embeddings(self, summaries: dict[int, str]) -> int:
        """Gera embeddings para sumários de páginas com source_type='page_summary'."""
        if not summaries:
            return 0

        page_ids = list(summaries.keys())
        texts = [summaries[pid] for pid in page_ids]

        embeddings = await self._embedding_gw.embed_batch(
            texts, task_type="RETRIEVAL_DOCUMENT"
        )

        records = [
            EmbeddingRecord(
                source_type="page_summary",
                source_id=page_id,
                embedding=emb,
                source_text_hash="",
                model_name=self._model_name,
                dimensions=len(emb),
            )
            for page_id, emb in zip(page_ids, embeddings)
        ]
        await self._emb_repo.save_batch(records)
        return len(records)

    async def _generate_section_embeddings(
        self, sections_data: list[dict], section_ids: list[int]
    ) -> int:
        """Gera embeddings para sumários de seções com source_type='section_summary'."""
        if not section_ids:
            return 0

        texts = [s["summary"] for s in sections_data[: len(section_ids)]]
        embeddings = await self._embedding_gw.embed_batch(
            texts, task_type="RETRIEVAL_DOCUMENT"
        )

        records = [
            EmbeddingRecord(
                source_type="section_summary",
                source_id=sec_id,
                embedding=emb,
                source_text_hash="",
                model_name=self._model_name,
                dimensions=len(emb),
            )
            for sec_id, emb in zip(section_ids, embeddings)
        ]
        await self._emb_repo.save_batch(records)
        return len(records)

    # ── Template methods (implementados pelo adapter PostgreSQL) ──────────────

    async def _get_covered_page_ids(self) -> set[int]:
        """IDs de páginas que já têm embedding page_summary."""
        raise NotImplementedError

    async def _fetch_pending_pages_from_db(
        self, covered: set[int], limit: int
    ) -> list[dict]:
        """Busca páginas sem cobertura de sumário LLM."""
        raise NotImplementedError

    async def _fetch_single_page_from_db(self, page_id: int) -> dict | None:
        """Busca uma única página pelo ID."""
        raise NotImplementedError

    async def _update_page_summaries(self, summaries: dict[int, str]) -> None:
        """Atualiza pages.content_summary para cada página do dicionário."""
        raise NotImplementedError

    async def _get_covered_document_ids(self) -> set[int]:
        """IDs de documentos que já têm seções sumarizadas."""
        raise NotImplementedError

    async def _fetch_pending_documents_from_db(
        self, covered: set[int], limit: int
    ) -> list[dict]:
        """Busca documentos longos sem seções sumarizadas."""
        raise NotImplementedError

    async def _save_section_summaries(
        self, document_id: int, sections_data: list[dict]
    ) -> list[int]:
        """Persiste seções na tabela section_summaries. Retorna IDs gerados."""
        raise NotImplementedError

    async def _count_total_pages(self) -> int:
        """Conta total de páginas com conteúdo."""
        raise NotImplementedError

    async def _count_long_documents(self) -> int:
        """Conta documentos com num_pages > section_min_pages."""
        raise NotImplementedError
