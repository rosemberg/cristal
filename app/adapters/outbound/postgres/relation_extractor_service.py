"""PostgresRelationExtractorService: implementa os template methods do RelationExtractor."""

from __future__ import annotations

import logging

import asyncpg

from app.adapters.outbound.postgres.relation_repo import PostgresRelationRepository
from app.domain.services.relation_extractor import RelationExtractor

logger = logging.getLogger(__name__)


class PostgresRelationExtractorService(RelationExtractor):
    """Extrator de relações concreto com acesso direto ao pool asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        super().__init__(PostgresRelationRepository(pool))
        self._pool = pool

    # ── Template methods ─────────────────────────────────────────────────────

    async def _fetch_entity_groups(
        self, batch_size: int, offset: int
    ) -> list[dict]:
        """Retorna grupos de páginas que compartilham o mesmo entity_key.

        Cada grupo: {page_id, entity_type, entity_key, peer_page_ids}.
        Estratégia: self-join em page_entities pelo campo entity_value.
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    pe1.page_id,
                    pe1.entity_type,
                    pe1.entity_value AS entity_key,
                    array_agg(DISTINCT pe2.page_id) AS peer_page_ids
                FROM page_entities pe1
                JOIN page_entities pe2
                     ON  pe2.entity_value = pe1.entity_value
                     AND pe2.entity_type  = pe1.entity_type
                     AND pe2.page_id      <> pe1.page_id
                GROUP BY pe1.page_id, pe1.entity_type, pe1.entity_value
                ORDER BY pe1.page_id
                LIMIT $1 OFFSET $2
                """,
                batch_size,
                offset,
            )
        return [
            {
                "page_id":      r["page_id"],
                "entity_type":  r["entity_type"],
                "entity_key":   r["entity_key"],
                "peer_page_ids": list(r["peer_page_ids"]),
            }
            for r in rows
        ]

    async def _fetch_pages_with_content(
        self, batch_size: int, offset: int
    ) -> list[dict]:
        """Retorna páginas com main_content não vazio."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, url, main_content
                FROM pages
                WHERE main_content IS NOT NULL
                  AND main_content <> ''
                ORDER BY id
                LIMIT $1 OFFSET $2
                """,
                batch_size,
                offset,
            )
        return [dict(r) for r in rows]

    async def _resolve_page_id_by_url(self, url: str) -> int | None:
        """Tenta resolver uma URL para page_id via lookup exato ou por sufixo."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id FROM pages WHERE url = $1 LIMIT 1", url
            )
            if row:
                return row["id"]
            # Tenta sufixo (URL relativa)
            row = await conn.fetchrow(
                "SELECT id FROM pages WHERE url LIKE '%' || $1 LIMIT 1", url
            )
        return row["id"] if row else None
