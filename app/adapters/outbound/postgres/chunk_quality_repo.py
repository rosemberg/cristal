"""PostgreSQL adapter: ChunkQualityRepository (Fase 5 NOVO_RAG)."""

from __future__ import annotations

import logging

import asyncpg

from app.domain.ports.outbound.chunk_quality_repository import ChunkQualityRepository
from app.domain.value_objects.chunk_quality import ChunkQualityResult, QualityReport

logger = logging.getLogger(__name__)

_VALID_TABLES = frozenset({"document_chunks", "page_chunks"})


def _check_table(table: str) -> None:
    if table not in _VALID_TABLES:
        raise ValueError(f"Tabela inválida: {table!r}. Válidas: {sorted(_VALID_TABLES)}")


class PostgresChunkQualityRepository(ChunkQualityRepository):
    """Persistência de qualidade de chunks via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_quality_batch(self, results: list[ChunkQualityResult]) -> None:
        if not results:
            return

        # Agrupa por tabela para minimizar round-trips
        by_table: dict[str, list[ChunkQualityResult]] = {}
        for r in results:
            by_table.setdefault(r.source_table, []).append(r)

        async with self._pool.acquire() as conn:
            for table, rows in by_table.items():
                _check_table(table)
                await conn.executemany(
                    f"""
                    UPDATE {table}
                    SET quality_score = $2,
                        quality_flags = $3,
                        quarantined   = $4
                    WHERE id = $1
                    """,
                    [
                        (r.chunk_id, r.score, r.flags, r.quarantined)
                        for r in rows
                    ],
                )

    async def fetch_unscored_chunks(
        self,
        table: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        _check_table(table)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, chunk_text
                FROM {table}
                WHERE quality_score IS NULL
                ORDER BY id
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [dict(r) for r in rows]

    async def count_unscored(self, table: str) -> int:
        _check_table(table)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT COUNT(*) AS total FROM {table} WHERE quality_score IS NULL"
            )
        return row["total"] if row else 0

    async def fetch_all_texts(
        self,
        table: str,
        limit: int = 5000,
        offset: int = 0,
    ) -> list[dict]:
        _check_table(table)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT id, chunk_text
                FROM {table}
                WHERE quarantined = false
                ORDER BY id
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
        return [dict(r) for r in rows]

    async def mark_duplicates_quarantined(
        self,
        table: str,
        duplicate_ids: list[int],
    ) -> int:
        _check_table(table)
        if not duplicate_ids:
            return 0
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"""
                UPDATE {table}
                SET quarantined   = true,
                    quality_flags = ARRAY(
                        SELECT DISTINCT unnest(quality_flags || ARRAY['duplicate'])
                    )
                WHERE id = ANY($1::int[])
                """,
                duplicate_ids,
            )
        return int(result.split()[-1])

    async def get_report(self) -> QualityReport:
        report = QualityReport()
        async with self._pool.acquire() as conn:
            for table in ("document_chunks", "page_chunks"):
                rows = await conn.fetch(
                    f"""
                    SELECT
                        COUNT(*) FILTER (WHERE quality_score IS NOT NULL) AS scored,
                        COUNT(*) FILTER (WHERE quarantined = true)         AS quarantined
                    FROM {table}
                    """
                )
                if rows:
                    report.chunks_scored      += rows[0]["scored"]
                    report.chunks_quarantined += rows[0]["quarantined"]

            # Distribuição de scores (ambas as tabelas, union)
            dist_rows = await conn.fetch(
                """
                SELECT floor(quality_score * 10) / 10 AS bucket, COUNT(*) AS cnt
                FROM (
                    SELECT quality_score FROM document_chunks WHERE quality_score IS NOT NULL
                    UNION ALL
                    SELECT quality_score FROM page_chunks     WHERE quality_score IS NOT NULL
                ) t
                GROUP BY bucket
                ORDER BY bucket
                """
            )
            for r in dist_rows:
                bucket = f"{r['bucket']:.1f}"
                report.score_distribution[bucket] = r["cnt"]

            # Contagem de flags
            flag_rows = await conn.fetch(
                """
                SELECT flag, COUNT(*) AS cnt
                FROM (
                    SELECT unnest(quality_flags) AS flag FROM document_chunks
                    UNION ALL
                    SELECT unnest(quality_flags) AS flag FROM page_chunks
                ) t
                GROUP BY flag
                """
            )
            for r in flag_rows:
                report.flag_counts[r["flag"]] = r["cnt"]

        return report
