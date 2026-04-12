"""PostgreSQL adapter: PageRepository.

Persiste páginas crawleadas via upsert idempotente (ON CONFLICT).
Todas as operações dentro de uma transação por página.
"""

from __future__ import annotations

import json
import logging

import asyncpg

from app.domain.ports.outbound.page_repository import (
    CrawledPage,
    LinkCheckInfo,
    PageCheckInfo,
    PageRepository,
)

logger = logging.getLogger(__name__)


class PostgresPageRepository(PageRepository):
    """Upsert idempotente de páginas e dados associados via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def upsert_page(self, data: CrawledPage) -> None:
        """Persiste uma página e seus vínculos dentro de uma transação atômica.

        Estratégia:
        - pages         → ON CONFLICT (url) DO UPDATE SET (todos os campos)
        - documents     → ON CONFLICT (page_url, document_url) DO UPDATE SET
        - page_links    → ON CONFLICT (source_url, target_url) DO NOTHING
        - navigation_tree → ON CONFLICT (parent_url, child_url) DO UPDATE SET child_title
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # ── 1. Upsert da página principal ─────────────────────────────
                await conn.execute(
                    """
                    INSERT INTO pages (
                        url, title, description, main_content, content_summary,
                        category, subcategory, content_type, depth, parent_url,
                        breadcrumb, tags, last_modified, extracted_at
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9, $10,
                        $11::jsonb, $12, $13, NOW()
                    )
                    ON CONFLICT (url) DO UPDATE SET
                        title           = EXCLUDED.title,
                        description     = EXCLUDED.description,
                        main_content    = EXCLUDED.main_content,
                        content_summary = EXCLUDED.content_summary,
                        category        = EXCLUDED.category,
                        subcategory     = EXCLUDED.subcategory,
                        content_type    = EXCLUDED.content_type,
                        depth           = EXCLUDED.depth,
                        parent_url      = EXCLUDED.parent_url,
                        breadcrumb      = EXCLUDED.breadcrumb,
                        tags            = EXCLUDED.tags,
                        last_modified   = EXCLUDED.last_modified,
                        extracted_at    = NOW()
                    """,
                    data.url,
                    data.title or "",
                    data.description or None,
                    data.main_content or None,
                    data.content_summary or None,
                    data.category or None,
                    data.subcategory or None,
                    data.content_type,
                    data.depth,
                    data.parent_url or None,
                    json.dumps(data.breadcrumb, ensure_ascii=False),
                    data.tags,
                    data.last_modified,
                )

                # ── 2. Upsert de documentos vinculados ────────────────────────
                if data.documents:
                    await conn.executemany(
                        """
                        INSERT INTO documents
                            (page_url, document_url, document_title, document_type, context)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (page_url, document_url) DO UPDATE SET
                            document_title = EXCLUDED.document_title,
                            document_type  = EXCLUDED.document_type,
                            context        = EXCLUDED.context
                        """,
                        [
                            (
                                data.url,
                                doc.document_url,
                                doc.document_title or None,
                                doc.document_type,
                                doc.context or None,
                            )
                            for doc in data.documents
                        ],
                    )

                # ── 3. Upsert de links internos ───────────────────────────────
                if data.internal_links:
                    await conn.executemany(
                        """
                        INSERT INTO page_links
                            (source_url, target_url, link_title, link_type)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (source_url, target_url) DO NOTHING
                        """,
                        [
                            (
                                data.url,
                                link.target_url,
                                link.link_title or None,
                                link.link_type,
                            )
                            for link in data.internal_links
                        ],
                    )

                # ── 4. Upsert na navigation_tree ──────────────────────────────
                if data.parent_url:
                    await conn.execute(
                        """
                        INSERT INTO navigation_tree (parent_url, child_url, child_title)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (parent_url, child_url) DO UPDATE SET
                            child_title = EXCLUDED.child_title
                        """,
                        data.parent_url,
                        data.url,
                        data.title or "",
                    )

                logger.debug("Upsert OK: %s", data.url)

    async def count_pages(self) -> int:
        """Retorna o total de páginas no banco."""
        async with self._pool.acquire() as conn:
            result: int | None = await conn.fetchval("SELECT COUNT(*) FROM pages")
        return result or 0

    async def list_all_urls(self) -> list[PageCheckInfo]:
        """Retorna URL e título de todas as páginas para health check."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT url, title FROM pages ORDER BY url"
            )
        return [PageCheckInfo(url=r["url"], title=r["title"] or "") for r in rows]

    async def list_all_links(self) -> list[LinkCheckInfo]:
        """Retorna todos os links de page_links para health check."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT target_url, link_title, source_url
                FROM page_links
                ORDER BY target_url
                """
            )
        return [
            LinkCheckInfo(
                url=r["target_url"],
                title=r["link_title"] or "",
                parent_page_url=r["source_url"],
            )
            for r in rows
        ]

    async def list_known_urls(self) -> set[str]:
        """Retorna conjunto de URLs já persistidas no banco."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT url FROM pages")
        return {r["url"] for r in rows}
