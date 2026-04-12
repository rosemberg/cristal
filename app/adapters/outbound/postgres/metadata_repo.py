"""PostgreSQL adapter: MetadataRepository.

Persiste e consulta entidades e tags de páginas nas tabelas `page_entities`
e `page_tags` (criadas na migration 009).
"""

from __future__ import annotations

import logging

import asyncpg

from app.domain.ports.outbound.metadata_repository import MetadataRepository
from app.domain.value_objects.enriched_metadata import PageEntity, PageTag

logger = logging.getLogger(__name__)


class PostgresMetadataRepository(MetadataRepository):
    """Implementa MetadataRepository via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_entities_batch(self, entities: list[PageEntity]) -> list[int]:
        """Insere entidades e retorna lista de IDs gerados na mesma ordem."""
        if not entities:
            return []

        ids: list[int] = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for e in entities:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO page_entities
                            (page_id, entity_type, entity_value, raw_text, confidence)
                        VALUES ($1, $2, $3, $4, $5)
                        RETURNING id
                        """,
                        e.page_id,
                        e.entity_type,
                        e.entity_value,
                        e.raw_text,
                        e.confidence,
                    )
                    ids.append(row["id"])

        logger.debug("MetadataRepo: %d entidades salvas", len(entities))
        return ids

    async def save_tags_batch(self, tags: list[PageTag]) -> None:
        """Insere tags. ON CONFLICT DO NOTHING preserva a UNIQUE(page_id, tag)."""
        if not tags:
            return

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for t in tags:
                    await conn.execute(
                        """
                        INSERT INTO page_tags (page_id, tag, confidence)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (page_id, tag) DO NOTHING
                        """,
                        t.page_id,
                        t.tag,
                        t.confidence,
                    )

        logger.debug("MetadataRepo: %d tags salvas", len(tags))

    async def get_covered_page_ids(self) -> set[int]:
        """Retorna IDs de páginas com pelo menos uma tag (Etapa B concluída)."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT page_id FROM page_tags"
            )
        return {r["page_id"] for r in rows}

    async def delete_by_page(self, page_id: int) -> None:
        """Remove todas as entidades e tags de uma página."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM page_entities WHERE page_id = $1", page_id
                )
                await conn.execute(
                    "DELETE FROM page_tags WHERE page_id = $1", page_id
                )
        logger.debug("MetadataRepo: entidades e tags removidas para página %d", page_id)

    async def delete_entities_by_page(self, page_id: int) -> None:
        """Remove apenas as entidades de uma página (sem apagar tags)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM page_entities WHERE page_id = $1", page_id
            )

    async def get_status(self) -> dict[str, object]:
        """Retorna estatísticas de entidades por tipo e tags por nome."""
        async with self._pool.acquire() as conn:
            entity_rows = await conn.fetch(
                """
                SELECT entity_type, COUNT(*) AS n
                FROM page_entities
                GROUP BY entity_type
                ORDER BY n DESC
                """
            )
            tag_rows = await conn.fetch(
                """
                SELECT tag, COUNT(*) AS n
                FROM page_tags
                GROUP BY tag
                ORDER BY n DESC
                """
            )
            total_entities = await conn.fetchval("SELECT COUNT(*) FROM page_entities")
            total_tags = await conn.fetchval("SELECT COUNT(*) FROM page_tags")

        return {
            "total_entities": int(total_entities or 0),
            "total_tags": int(total_tags or 0),
            "entities_by_type": {r["entity_type"]: int(r["n"]) for r in entity_rows},
            "tags_by_name": {r["tag"]: int(r["n"]) for r in tag_rows},
        }
