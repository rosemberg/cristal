"""PostgreSQL adapter: RelationRepository (Fase 6 NOVO_RAG)."""

from __future__ import annotations

import logging

import asyncpg

from app.domain.ports.outbound.relation_repository import RelationRepository
from app.domain.value_objects.document_relation import DocumentRelation, RelationExtractionResult

logger = logging.getLogger(__name__)


class PostgresRelationRepository(RelationRepository):
    """Persistência do grafo de relações via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_relations_batch(self, relations: list[DocumentRelation]) -> int:
        if not relations:
            return 0
        async with self._pool.acquire() as conn:
            result = await conn.executemany(
                """
                INSERT INTO document_relations
                    (source_page_id, target_page_id, target_url,
                     relation_type, context, entity_key,
                     confidence, strategy)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT DO NOTHING
                """,
                [
                    (
                        r.source_page_id,
                        r.target_page_id,
                        r.target_url,
                        r.relation_type,
                        r.context,
                        r.entity_key,
                        r.confidence,
                        r.strategy,
                    )
                    for r in relations
                ],
            )
        # executemany não retorna contagem diretamente; usamos len como proxy
        return len(relations)

    async def get_related_pages(
        self,
        page_id: int,
        relation_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[DocumentRelation]:
        type_filter = ""
        params: list = [page_id, page_id, limit]
        if relation_types:
            type_filter = "AND dr.relation_type = ANY($4::text[])"
            params.append(relation_types)

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, source_page_id, target_page_id, target_url,
                       relation_type, context, entity_key, confidence, strategy
                FROM document_relations dr
                WHERE (source_page_id = $1 OR target_page_id = $2)
                {type_filter}
                ORDER BY confidence DESC
                LIMIT $3
                """,
                *params,
            )
        return [self._row_to_relation(r) for r in rows]

    async def get_covered_page_ids(self, strategy: str | None = None) -> set[int]:
        async with self._pool.acquire() as conn:
            if strategy:
                rows = await conn.fetch(
                    "SELECT DISTINCT source_page_id FROM document_relations WHERE strategy = $1",
                    strategy,
                )
            else:
                rows = await conn.fetch(
                    "SELECT DISTINCT source_page_id FROM document_relations"
                )
        return {r["source_page_id"] for r in rows}

    async def get_status(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT strategy, relation_type, COUNT(*) AS cnt
                FROM document_relations
                GROUP BY strategy, relation_type
                ORDER BY strategy, relation_type
                """
            )
        counts: dict[str, int] = {}
        for r in rows:
            counts[f"{r['strategy']}/{r['relation_type']}"] = r["cnt"]
        return counts

    @staticmethod
    def _row_to_relation(row: asyncpg.Record) -> DocumentRelation:
        return DocumentRelation(
            id=row["id"],
            source_page_id=row["source_page_id"],
            target_page_id=row["target_page_id"],
            target_url=row["target_url"],
            relation_type=row["relation_type"],
            context=row["context"],
            entity_key=row["entity_key"],
            confidence=row["confidence"],
            strategy=row["strategy"],
        )
