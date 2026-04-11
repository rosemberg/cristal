"""PostgreSQL adapter: SearchRepository.

Implementa busca full-text em pages e document_chunks usando o índice GIN
TSVECTOR do PostgreSQL com dicionário 'portuguese'.
"""

from __future__ import annotations

import json
import logging

import asyncpg

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document_table import DocumentTable
from app.domain.entities.page import Page
from app.domain.ports.outbound.search_repository import SearchRepository
from app.domain.value_objects.search_result import ChunkMatch, PageMatch

logger = logging.getLogger(__name__)


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
        if not query.strip():
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT p.*,
                       ts_rank(p.search_vector, q) AS rank,
                       ts_headline(
                           'portuguese',
                           COALESCE(p.description, p.title),
                           q,
                           'MaxFragments=1,MaxWords=20,MinWords=5'
                       ) AS highlight
                FROM pages p,
                     plainto_tsquery('portuguese', $1) q
                WHERE p.search_vector @@ q
                ORDER BY rank DESC
                LIMIT $2
                """,
                query,
                top_k,
            )
        return [
            PageMatch(page=_record_to_page(r), score=float(r["rank"]), highlight=r["highlight"])
            for r in rows
        ]

    async def search_chunks(self, query: str, top_k: int = 5) -> list[ChunkMatch]:
        if not query.strip():
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT dc.*,
                       ts_rank(dc.search_vector, q) AS rank,
                       COALESCE(cont.document_title, dc.document_url) AS doc_title
                FROM document_chunks dc
                JOIN document_contents cont ON cont.document_url = dc.document_url,
                     plainto_tsquery('portuguese', $1) q
                WHERE dc.search_vector @@ q
                ORDER BY rank DESC
                LIMIT $2
                """,
                query,
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
        if not query.strip():
            return []
        pattern = f"%{query}%"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM document_tables
                WHERE search_text ILIKE $1
                   OR caption ILIKE $1
                ORDER BY id
                LIMIT 10
                """,
                pattern,
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
