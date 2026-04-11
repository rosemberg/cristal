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


class PostgresSearchRepository(SearchRepository):
    """Busca full-text contra pages e document_chunks via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

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
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT dc.*,
                       ts_rank(dc.search_vector, q) AS rank,
                       COALESCE(cont.document_title, dc.document_url) AS doc_title
                FROM document_chunks dc
                JOIN document_contents cont ON cont.document_url = dc.document_url,
                     plainto_tsquery('cristal_pt', $1) q
                WHERE dc.search_vector @@ q
                ORDER BY rank DESC
                LIMIT $2
                """,
                cleaned,
                top_k,
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
        # Busca tabelas cujo conteúdo (headers, rows, caption, document_url)
        # contenha QUALQUER dos termos relevantes da query.
        words = cleaned.split()
        conditions = " OR ".join(
            f"COALESCE(search_text, '') ILIKE ${i} "
            f"OR COALESCE(caption, '') ILIKE ${i} "
            f"OR headers::text ILIKE ${i} "
            f"OR rows::text ILIKE ${i} "
            f"OR document_url ILIKE ${i}"
            for i in range(1, len(words) + 1)
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
        model_name = "text-embedding-005"

        if source_type == "chunk":
            return await self._search_semantic_chunks(vec_str, model_name, top_k, filters)
        if source_type == "page":
            return await self._search_semantic_pages(vec_str, model_name, top_k, filters)
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
