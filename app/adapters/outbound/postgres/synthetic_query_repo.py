"""PostgreSQL adapter: SyntheticQueryRepository.

Persiste e consulta perguntas sintéticas na tabela `synthetic_queries`
(criada na migration 006).
"""

from __future__ import annotations

import logging

import asyncpg

from app.domain.ports.outbound.synthetic_query_repository import SyntheticQueryRepository
from app.domain.value_objects.synthetic_query import SyntheticQuery

logger = logging.getLogger(__name__)


class PostgresSyntheticQueryRepository(SyntheticQueryRepository):
    """Implementa SyntheticQueryRepository via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_batch(self, queries: list[SyntheticQuery]) -> list[int]:
        """Insere perguntas e retorna lista de IDs gerados na mesma ordem."""
        if not queries:
            return []

        ids: list[int] = []
        async with self._pool.acquire() as conn:
            for q in queries:
                row = await conn.fetchrow(
                    """
                    INSERT INTO synthetic_queries
                        (source_type, source_id, question, model_used)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    q.source_type,
                    q.source_id,
                    q.question,
                    q.model_used,
                )
                ids.append(row["id"])

        logger.debug(
            "SyntheticQueryRepo: %d perguntas salvas (tipo=%s)",
            len(queries),
            queries[0].source_type if queries else "?",
        )
        return ids

    async def get_covered_source_ids(self, source_type: str) -> set[int]:
        """Retorna conjunto de source_ids com pelo menos 1 pergunta sintética."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT source_id
                FROM synthetic_queries
                WHERE source_type = $1
                """,
                source_type,
            )
        return {row["source_id"] for row in rows}

    async def delete_by_source(self, source_type: str, source_id: int) -> None:
        """Remove todas as perguntas de uma fonte."""
        async with self._pool.acquire() as conn:
            deleted = await conn.execute(
                """
                DELETE FROM synthetic_queries
                WHERE source_type = $1 AND source_id = $2
                """,
                source_type,
                source_id,
            )
        logger.debug(
            "SyntheticQueryRepo: deletadas perguntas de %s/%d (%s)",
            source_type, source_id, deleted,
        )

    async def count_by_source_type(self, source_type: str) -> int:
        """Retorna total de perguntas para o source_type dado."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM synthetic_queries WHERE source_type = $1",
                source_type,
            )
        return int(row["n"]) if row else 0

    async def get_status(self) -> dict[str, int]:
        """Retorna {source_type: count} para todos os tipos existentes."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT source_type, COUNT(*) AS n
                FROM synthetic_queries
                GROUP BY source_type
                """
            )
        return {row["source_type"]: int(row["n"]) for row in rows}
