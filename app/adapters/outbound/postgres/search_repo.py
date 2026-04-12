"""PostgreSQL adapter: SearchRepository.

Implementa busca full-text em pages e document_chunks usando o índice GIN
TSVECTOR do PostgreSQL com a configuração customizada 'cristal_pt' (portuguese
+ unaccent), que normaliza acentuação antes do stemmer para que "DIARIAS" e
"diárias" produzam o mesmo lexema.
"""

from __future__ import annotations

import json
import logging
import re

import asyncpg

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document_table import DocumentTable
from app.domain.entities.page import Page
from app.domain.ports.outbound.search_repository import SearchRepository
from app.domain.value_objects.search_result import ChunkMatch, PageMatch, SemanticMatch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Palavras interrogativas e filler que não são stop words do dicionário
# 'portuguese' do PostgreSQL, mas poluem a busca full-text (AND) quando
# o usuário faz perguntas em linguagem natural.
# ---------------------------------------------------------------------------
_QUERY_STOPWORDS = {
    "quais", "qual", "como", "onde", "quando", "quanto", "quanta",
    "quantos", "quantas", "porque", "porquê", "será", "seria",
    "poderia", "pode", "posso", "gostaria", "preciso", "quero",
    "favor", "obrigado", "obrigada", "por", "sobre", "entre",
    "durante", "após", "antes", "através", "ainda", "também",
    "muito", "mais", "menos", "todos", "todas", "cada", "outro",
    "outra", "outros", "outras", "esse", "essa", "esses", "essas",
    "este", "esta", "estes", "estas", "aquele", "aquela", "aqueles",
    "aquelas", "isso", "isto", "aquilo", "foram", "foram", "sido",
    "sendo", "está", "estão", "são", "era", "eram",
    # Verbos de comando/imperativo frequentes em perguntas ao chatbot
    # (ao serem stemados pelo dicionário português, geram tokens raros como
    # 'list', 'mostr', 'apresent' que não existem nos documentos indexados,
    # fazendo a query AND retornar zero resultados)
    "liste", "listar", "mostre", "mostrar", "apresente",
    "apresentar", "informe", "informar", "busque", "buscar",
    "encontre", "encontrar", "traga", "trazer", "diga", "dizer",
    "me", "mim", "meu", "minha", "meus", "minhas",
    # Artigos e preposições portuguesas — irrelevantes para busca ILIKE
    # em search_tables (WHERE col ILIKE '%de%' retorna quase tudo)
    "os", "as", "de", "do", "da", "dos", "das", "ao", "aos",
    "à", "às", "no", "na", "nos", "nas", "em", "um", "uma",
    "uns", "umas", "o", "a", "e",
}

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _clean_query(raw_query: str) -> str:
    """Remove pontuação e palavras interrogativas/filler da query do usuário."""
    text = _PUNCTUATION_RE.sub(" ", raw_query)
    words = [w for w in text.lower().split() if w not in _QUERY_STOPWORDS]
    return " ".join(words)


def _parse_jsonb(value: object) -> list:  # type: ignore[type-arg]
    """Normaliza valor JSONB: asyncpg pode retornar string ou lista nativa."""
    if value is None:
        return []
    if isinstance(value, str):
        return json.loads(value)  # type: ignore[no-any-return]
    return list(value)  # type: ignore[arg-type]


def _record_to_page(row: asyncpg.Record) -> Page:
    return Page(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        content_type=row["content_type"] or "page",
        depth=row["depth"] if row["depth"] is not None else 0,
        description=row["description"],
        main_content=row["main_content"],
        content_summary=row["content_summary"],
        category=row["category"],
        subcategory=row["subcategory"],
        parent_url=row["parent_url"],
        breadcrumb=list(row["breadcrumb"]) if row["breadcrumb"] else [],
        tags=list(row["tags"]) if row["tags"] else [],
    )


_YEAR_RE = re.compile(r"\b(20\d{2})\b")


class PostgresSearchRepository(SearchRepository):
    """Busca full-text contra pages e document_chunks via asyncpg."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        embedding_model_name: str = "gemini-embedding-001",
    ) -> None:
        self._pool = pool
        self._embedding_model_name = embedding_model_name

    async def search_pages(self, query: str, top_k: int = 5) -> list[PageMatch]:
        cleaned = _clean_query(query)
        if not cleaned:
            return []
        logger.debug("search_pages cleaned query: %r -> %r", query, cleaned)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT p.*,
                       ts_rank(p.search_vector, q) AS rank,
                       ts_headline(
                           'cristal_pt',
                           COALESCE(p.description, p.title),
                           q,
                           'MaxFragments=1,MaxWords=20,MinWords=5'
                       ) AS highlight
                FROM pages p,
                     plainto_tsquery('cristal_pt', $1) q
                WHERE p.search_vector @@ q
                ORDER BY rank DESC
                LIMIT $2
                """,
                cleaned,
                top_k,
            )
        return [
            PageMatch(page=_record_to_page(r), score=float(r["rank"]), highlight=r["highlight"])
            for r in rows
        ]

    async def search_chunks(self, query: str, top_k: int = 5) -> list[ChunkMatch]:
        cleaned = _clean_query(query)
        if not cleaned:
            return []
        logger.debug("search_chunks cleaned query: %r -> %r", query, cleaned)

        # Extrai anos presentes na query para busca complementar por document_url
        years = _YEAR_RE.findall(query)
        url_patterns = [f"%{y}%" for y in years]

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT sub.id, sub.document_url, sub.chunk_index, sub.chunk_text,
                       sub.token_count, sub.section_title, sub.page_number,
                       sub.rank, sub.doc_title
                FROM (
                    -- FTS em document_chunks
                    SELECT dc.id, dc.document_url, dc.chunk_index, dc.chunk_text,
                           dc.token_count, dc.section_title, dc.page_number,
                           ts_rank(dc.search_vector, q) AS rank,
                           COALESCE(cont.document_title, dc.document_url) AS doc_title
                    FROM document_chunks dc
                    JOIN document_contents cont ON cont.document_url = dc.document_url,
                         plainto_tsquery('cristal_pt', $1) q
                    WHERE dc.search_vector @@ q

                    UNION

                    -- Match por ano/termo na URL do documento (ex: "contrato-20-2025")
                    SELECT dc.id, dc.document_url, dc.chunk_index, dc.chunk_text,
                           dc.token_count, dc.section_title, dc.page_number,
                           0.05 AS rank,
                           COALESCE(cont.document_title, dc.document_url) AS doc_title
                    FROM document_chunks dc
                    JOIN document_contents cont ON cont.document_url = dc.document_url
                    WHERE cardinality($3::text[]) > 0
                      AND EXISTS (
                          SELECT 1 FROM unnest($3::text[]) AS pat
                          WHERE dc.document_url ILIKE pat
                      )

                    UNION

                    -- FTS em page_chunks (conteúdo direto de páginas)
                    -- IDs negativos para evitar colisão com document_chunks no RRF
                    SELECT -pc.id AS id, pc.page_url AS document_url,
                           pc.chunk_index, pc.chunk_text,
                           pc.token_count, NULL AS section_title, NULL AS page_number,
                           ts_rank(pc.search_vector, q) AS rank,
                           p.title AS doc_title
                    FROM page_chunks pc
                    JOIN pages p ON p.id = pc.page_id,
                         plainto_tsquery('cristal_pt', $1) q
                    WHERE pc.search_vector @@ q
                ) sub
                ORDER BY sub.rank DESC
                LIMIT $2
                """,
                cleaned,
                top_k,
                url_patterns,
            )
        return [
            ChunkMatch(
                chunk=DocumentChunk(
                    id=r["id"],
                    document_url=r["document_url"],
                    chunk_index=r["chunk_index"],
                    text=r["chunk_text"],
                    token_count=r["token_count"] or 0,
                    section_title=r["section_title"],
                    page_number=r["page_number"],
                ),
                document_title=r["doc_title"],
                document_url=r["document_url"],
                score=float(r["rank"]),
            )
            for r in rows
        ]

    async def search_tables(self, query: str) -> list[DocumentTable]:
        cleaned = _clean_query(query)
        if not cleaned:
            return []
        words = [w for w in cleaned.split() if len(w) >= 3]
        if not words:
            return []

        # Cada termo deve aparecer em PELO MENOS UMA das colunas da tabela
        # (AND entre termos, OR entre colunas de cada termo).
        # Isso evita falso-positivos gerados por palavras genéricas como
        # "os" ou "de" que existem como substring em quase toda URL/linha.
        def term_condition(param_idx: int) -> str:
            return (
                f"(COALESCE(search_text, '') ILIKE ${param_idx} "
                f"OR COALESCE(caption, '') ILIKE ${param_idx} "
                f"OR headers::text ILIKE ${param_idx} "
                f"OR rows::text ILIKE ${param_idx} "
                f"OR document_url ILIKE ${param_idx})"
            )

        conditions = " AND ".join(
            term_condition(i) for i in range(1, len(words) + 1)
        )
        patterns = [f"%{w}%" for w in words]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                FROM document_tables
                WHERE {conditions}
                ORDER BY id
                LIMIT 10
                """,
                *patterns,
            )
        return [
            DocumentTable(
                id=r["id"],
                document_url=r["document_url"],
                table_index=r["table_index"],
                headers=_parse_jsonb(r["headers"]),
                rows=[list(row) for row in _parse_jsonb(r["rows"])],
                page_number=r["page_number"],
                caption=r["caption"],
                num_rows=r["num_rows"],
                num_cols=r["num_cols"],
            )
            for r in rows
        ]

    async def search_semantic(
        self,
        query_embedding: list[float],
        source_type: str = "chunk",
        top_k: int = 5,
        filters: dict[str, object] | None = None,
    ) -> list[SemanticMatch]:
        """Busca semântica via cosine similarity na tabela embeddings + pgvector."""
        if not query_embedding:
            return []

        vec_str = "[" + ",".join(repr(x) for x in query_embedding) + "]"
        model_name = self._embedding_model_name

        if source_type == "chunk":
            return await self._search_semantic_chunks(vec_str, model_name, top_k, filters)
        if source_type == "page":
            return await self._search_semantic_pages(vec_str, model_name, top_k, filters)
        if source_type == "page_chunk":
            return await self._search_semantic_page_chunks(vec_str, model_name, top_k)
        logger.warning("search_semantic: source_type desconhecido '%s'", source_type)
        return []

    async def _search_semantic_chunks(
        self,
        vec_str: str,
        model_name: str,
        top_k: int,
        filters: dict[str, object] | None,
    ) -> list[SemanticMatch]:
        category_filter = (filters or {}).get("category")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.source_id,
                       e.source_type,
                       1 - (e.embedding <=> $1::vector) AS similarity,
                       dc.id            AS chunk_id,
                       dc.document_url,
                       dc.chunk_index,
                       dc.chunk_text,
                       dc.token_count,
                       dc.section_title,
                       dc.page_number,
                       COALESCE(cont.document_title, dc.document_url) AS doc_title
                FROM embeddings e
                JOIN document_chunks dc
                    ON dc.id = e.source_id
                JOIN document_contents cont
                    ON cont.document_url = dc.document_url
                LEFT JOIN documents d
                    ON d.document_url = dc.document_url
                LEFT JOIN pages p
                    ON p.url = d.page_url
                WHERE e.source_type = 'chunk'
                  AND e.model_name   = $2
                  AND ($3::text IS NULL OR p.category = $3)
                ORDER BY e.embedding <=> $1::vector
                LIMIT $4
                """,
                vec_str,
                model_name,
                category_filter,
                top_k,
            )
        results: list[SemanticMatch] = []
        for r in rows:
            chunk = DocumentChunk(
                id=r["chunk_id"],
                document_url=r["document_url"],
                chunk_index=r["chunk_index"],
                text=r["chunk_text"],
                token_count=r["token_count"] or 0,
                section_title=r["section_title"],
                page_number=r["page_number"],
            )
            results.append(
                SemanticMatch(
                    source_id=r["source_id"],
                    source_type="chunk",
                    similarity=float(r["similarity"]),
                    document_url=r["document_url"],
                    document_title=r["doc_title"],
                    chunk=chunk,
                )
            )
        return results

    async def _search_semantic_pages(
        self,
        vec_str: str,
        model_name: str,
        top_k: int,
        filters: dict[str, object] | None,
    ) -> list[SemanticMatch]:
        category_filter = (filters or {}).get("category")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.source_id,
                       e.source_type,
                       1 - (e.embedding <=> $1::vector) AS similarity,
                       p.*
                FROM embeddings e
                JOIN pages p ON p.id = e.source_id
                WHERE e.source_type = 'page'
                  AND e.model_name   = $2
                  AND ($3::text IS NULL OR p.category = $3)
                ORDER BY e.embedding <=> $1::vector
                LIMIT $4
                """,
                vec_str,
                model_name,
                category_filter,
                top_k,
            )
        results: list[SemanticMatch] = []
        for r in rows:
            page = _record_to_page(r)
            results.append(
                SemanticMatch(
                    source_id=r["source_id"],
                    source_type="page",
                    similarity=float(r["similarity"]),
                    document_url=page.url,
                    document_title=page.title,
                    page=page,
                )
            )
        return results

    async def _search_semantic_page_chunks(
        self,
        vec_str: str,
        model_name: str,
        top_k: int,
    ) -> list[SemanticMatch]:
        """Busca semântica em page_chunks via cosine similarity."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT e.source_id,
                       1 - (e.embedding <=> $1::vector) AS similarity,
                       pc.id            AS chunk_id,
                       pc.page_url,
                       pc.chunk_index,
                       pc.chunk_text,
                       pc.token_count,
                       p.title          AS page_title
                FROM embeddings e
                JOIN page_chunks pc ON pc.id = e.source_id
                JOIN pages p ON p.id = pc.page_id
                WHERE e.source_type = 'page_chunk'
                  AND e.model_name  = $2
                ORDER BY e.embedding <=> $1::vector
                LIMIT $3
                """,
                vec_str,
                model_name,
                top_k,
            )
        results: list[SemanticMatch] = []
        for r in rows:
            # IDs negativos para evitar colisão com document_chunks no RRF
            chunk = DocumentChunk(
                id=-r["chunk_id"],
                document_url=r["page_url"],
                chunk_index=r["chunk_index"],
                text=r["chunk_text"],
                token_count=r["token_count"] or 0,
                section_title=None,
                page_number=None,
            )
            results.append(
                SemanticMatch(
                    source_id=-r["chunk_id"],
                    source_type="page_chunk",
                    similarity=float(r["similarity"]),
                    document_url=r["page_url"],
                    document_title=r["page_title"],
                    chunk=chunk,
                )
            )
        return results

    async def get_categories(self) -> list[dict[str, object]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT category, COUNT(*) AS count
                FROM pages
                WHERE category IS NOT NULL
                GROUP BY category
                ORDER BY category
                """
            )
        return [{"name": r["category"], "count": int(r["count"])} for r in rows]

    async def get_stats(self) -> dict[str, object]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM pages)           AS total_pages,
                    (SELECT COUNT(*) FROM document_chunks) AS total_chunks,
                    (SELECT COUNT(*) FROM document_tables) AS total_tables,
                    (SELECT COUNT(*) FROM documents)       AS total_documents
                """
            )
        if row is None:
            return {
                "total_pages": 0,
                "total_chunks": 0,
                "total_tables": 0,
                "total_documents": 0,
            }
        return {
            "total_pages": int(row["total_pages"]),
            "total_chunks": int(row["total_chunks"]),
            "total_tables": int(row["total_tables"]),
            "total_documents": int(row["total_documents"]),
        }
