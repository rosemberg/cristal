"""PostgreSQL-backed MetadataEnricherService.

Estende o serviço de domínio com acesso direto ao pool asyncpg para:
- Buscar páginas pendentes (sem tags)
- Buscar páginas paginadas (para regex-all)
- Contagem de páginas totais
"""

from __future__ import annotations

import logging

import asyncpg

from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.ports.outbound.metadata_repository import MetadataRepository
from app.domain.services.metadata_enricher import MetadataEnricherService

logger = logging.getLogger(__name__)

_PAGE_COLUMNS = "id, title, category, subcategory, main_content"
_PAGE_WHERE = "main_content IS NOT NULL AND LENGTH(main_content) > 100"


class PostgresMetadataEnricherService(MetadataEnricherService):
    """Implementação concreta com acesso ao banco via asyncpg."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        llm_gateway: LLMGateway,
        metadata_repo: MetadataRepository,
        model_name: str = "gemini-2.5-flash-lite",
        llm_batch_size: int = 10,
    ) -> None:
        super().__init__(
            llm_gateway=llm_gateway,
            metadata_repo=metadata_repo,
            model_name=model_name,
            llm_batch_size=llm_batch_size,
        )
        self._pool = pool

    async def _fetch_pending_pages_from_db(
        self, covered: set[int], limit: int
    ) -> list[dict]:
        """Busca páginas com conteúdo que ainda não têm tags."""
        covered_list = list(covered) if covered else [-1]
        placeholders = ", ".join(f"${i + 2}" for i in range(len(covered_list)))

        query = f"""
            SELECT {_PAGE_COLUMNS}
            FROM pages
            WHERE id NOT IN ({placeholders})
              AND {_PAGE_WHERE}
            ORDER BY id
            LIMIT $1
        """  # noqa: S608

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, limit, *covered_list)

        return [_row_to_dict(r) for r in rows]

    async def _fetch_pages_paginated(self, offset: int, limit: int) -> list[dict]:
        """Busca páginas ordenadas por ID com OFFSET/LIMIT (para regex-all)."""
        query = f"""
            SELECT {_PAGE_COLUMNS}
            FROM pages
            WHERE {_PAGE_WHERE}
            ORDER BY id
            LIMIT $1 OFFSET $2
        """  # noqa: S608

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, limit, offset)

        return [_row_to_dict(r) for r in rows]

    async def _fetch_single_page_from_db(self, page_id: int) -> dict | None:
        """Busca uma única página pelo ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_PAGE_COLUMNS} FROM pages WHERE id = $1",  # noqa: S608
                page_id,
            )
        return _row_to_dict(row) if row else None

    async def _count_total_pages(self) -> int:
        async with self._pool.acquire() as conn:
            val = await conn.fetchval(
                f"SELECT COUNT(*) FROM pages WHERE {_PAGE_WHERE}"  # noqa: S608
            )
        return int(val or 0)


def _row_to_dict(row: asyncpg.Record) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "category": row["category"],
        "subcategory": row["subcategory"],
        "main_content": row["main_content"],
    }
