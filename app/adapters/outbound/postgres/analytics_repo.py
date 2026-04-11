"""PostgreSQL adapter: AnalyticsRepository.

Registra queries de usuários e agrega métricas de uso via asyncpg.
"""

from __future__ import annotations

import logging
from uuid import UUID

import asyncpg

from app.domain.ports.outbound.analytics_repository import AnalyticsRepository

logger = logging.getLogger(__name__)


class PostgresAnalyticsRepository(AnalyticsRepository):
    """Persistência de analytics em query_logs via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def log_query(
        self,
        session_id: UUID | None,
        query: str,
        intent_type: str,
        pages_found: int,
        chunks_found: int,
        tables_found: int,
        response_time_ms: int,
    ) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO query_logs
                    (session_id, query, intent_type, pages_found,
                     chunks_found, tables_found, response_time_ms)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
                """,
                session_id,
                query,
                intent_type,
                pages_found,
                chunks_found,
                tables_found,
                response_time_ms,
            )
        assert row is not None
        return int(row["id"])

    async def update_feedback(self, query_id: int, feedback: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE query_logs SET feedback = $1 WHERE id = $2",
                feedback,
                query_id,
            )

    async def get_metrics(self, days: int = 30) -> dict[str, object]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) AS total_queries,
                    COALESCE(AVG(response_time_ms), 0) AS avg_response_time_ms,
                    COUNT(CASE WHEN feedback = 'positive' THEN 1 END) AS positive_feedback,
                    COUNT(CASE WHEN feedback = 'negative' THEN 1 END) AS negative_feedback
                FROM query_logs
                WHERE created_at >= NOW() - ($1 * INTERVAL '1 day')
                """,
                days,
            )
        if row is None:
            return {
                "total_queries": 0,
                "avg_response_time_ms": 0.0,
                "positive_feedback": 0,
                "negative_feedback": 0,
            }
        return {
            "total_queries": int(row["total_queries"]),
            "avg_response_time_ms": float(row["avg_response_time_ms"]),
            "positive_feedback": int(row["positive_feedback"]),
            "negative_feedback": int(row["negative_feedback"]),
        }

    async def get_daily_stats(self, days: int = 30) -> list[dict[str, object]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    DATE(created_at) AS date,
                    COUNT(*) AS query_count,
                    COALESCE(AVG(response_time_ms), 0) AS avg_response_time_ms
                FROM query_logs
                WHERE created_at >= NOW() - ($1 * INTERVAL '1 day')
                GROUP BY DATE(created_at)
                ORDER BY date DESC
                """,
                days,
            )
        return [
            {
                "date": str(r["date"]),
                "query_count": int(r["query_count"]),
                "avg_response_time_ms": float(r["avg_response_time_ms"]),
            }
            for r in rows
        ]
