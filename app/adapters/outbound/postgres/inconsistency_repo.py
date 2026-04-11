"""PostgreSQL adapter: InconsistencyRepository.

Gerencia a tabela data_inconsistencies via asyncpg.
"""

from __future__ import annotations

import logging
from datetime import timezone

import asyncpg

from app.domain.ports.outbound.inconsistency_repository import InconsistencyRepository
from app.domain.value_objects.data_inconsistency import DataInconsistency
from app.domain.value_objects.inconsistency_summary import InconsistencySummary

logger = logging.getLogger(__name__)


def _record_to_inconsistency(row: asyncpg.Record) -> DataInconsistency:
    detected_at = row["detected_at"]
    last_checked_at = row["last_checked_at"]
    resolved_at = row["resolved_at"]

    # asyncpg retorna datetimes aware (UTC) — garantir timezone se necessário
    def _ensure_tz(dt):  # type: ignore[no-untyped-def]
        if dt is None:
            return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt

    return DataInconsistency(
        id=row["id"],
        resource_type=row["resource_type"],
        severity=row["severity"],
        inconsistency_type=row["inconsistency_type"],
        resource_url=row["resource_url"],
        resource_title=row["resource_title"],
        parent_page_url=row["parent_page_url"],
        detail=row["detail"],
        http_status=row["http_status"],
        error_message=row["error_message"],
        detected_at=_ensure_tz(detected_at),
        detected_by=row["detected_by"],
        status=row["status"],
        resolved_at=_ensure_tz(resolved_at),
        resolved_by=row["resolved_by"],
        resolution_note=row["resolution_note"],
        retry_count=row["retry_count"],
        last_checked_at=_ensure_tz(last_checked_at),
    )


class PostgresInconsistencyRepository(InconsistencyRepository):
    """Acesso à tabela data_inconsistencies via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save(self, inconsistency: DataInconsistency) -> int:
        async with self._pool.acquire() as conn:
            row_id: int = await conn.fetchval(
                """
                INSERT INTO data_inconsistencies (
                    resource_type, severity, inconsistency_type,
                    resource_url, resource_title, parent_page_url,
                    detail, http_status, error_message,
                    detected_at, detected_by,
                    status, resolved_at, resolved_by, resolution_note,
                    retry_count, last_checked_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10, $11, $12, $13, $14, $15, $16, $17
                )
                RETURNING id
                """,
                inconsistency.resource_type,
                inconsistency.severity,
                inconsistency.inconsistency_type,
                inconsistency.resource_url,
                inconsistency.resource_title,
                inconsistency.parent_page_url,
                inconsistency.detail,
                inconsistency.http_status,
                inconsistency.error_message,
                inconsistency.detected_at,
                inconsistency.detected_by,
                inconsistency.status,
                inconsistency.resolved_at,
                inconsistency.resolved_by,
                inconsistency.resolution_note,
                inconsistency.retry_count,
                inconsistency.last_checked_at,
            )
        return row_id

    async def upsert(
        self,
        resource_url: str,
        inconsistency_type: str,
        inconsistency: DataInconsistency,
    ) -> int:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                existing = await conn.fetchrow(
                    """
                    SELECT id, retry_count
                    FROM data_inconsistencies
                    WHERE resource_url = $1
                      AND inconsistency_type = $2
                      AND status = 'open'
                    LIMIT 1
                    """,
                    resource_url,
                    inconsistency_type,
                )
                if existing:
                    # Atualiza registro aberto existente
                    await conn.execute(
                        """
                        UPDATE data_inconsistencies
                        SET detail          = $2,
                            http_status     = $3,
                            error_message   = $4,
                            retry_count     = retry_count + 1,
                            last_checked_at = NOW()
                        WHERE id = $1
                        """,
                        existing["id"],
                        inconsistency.detail,
                        inconsistency.http_status,
                        inconsistency.error_message,
                    )
                    return existing["id"]
                else:
                    # Cria novo registro
                    row_id: int = await conn.fetchval(
                        """
                        INSERT INTO data_inconsistencies (
                            resource_type, severity, inconsistency_type,
                            resource_url, resource_title, parent_page_url,
                            detail, http_status, error_message,
                            detected_at, detected_by,
                            status, retry_count, last_checked_at
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8, $9,
                            $10, $11, 'open', $12, NOW()
                        )
                        RETURNING id
                        """,
                        inconsistency.resource_type,
                        inconsistency.severity,
                        inconsistency_type,
                        resource_url,
                        inconsistency.resource_title,
                        inconsistency.parent_page_url,
                        inconsistency.detail,
                        inconsistency.http_status,
                        inconsistency.error_message,
                        inconsistency.detected_at,
                        inconsistency.detected_by,
                        inconsistency.retry_count,
                    )
                    return row_id

    async def list_by_status(
        self,
        status: str = "open",
        resource_type: str | None = None,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DataInconsistency]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT *
                FROM data_inconsistencies
                WHERE status = $1
                  AND ($2::text IS NULL OR resource_type = $2)
                  AND ($3::text IS NULL OR severity = $3)
                ORDER BY detected_at DESC
                LIMIT $4 OFFSET $5
                """,
                status,
                resource_type,
                severity,
                limit,
                offset,
            )
        return [_record_to_inconsistency(r) for r in rows]

    async def count_by_status(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT status, COUNT(*) AS cnt
                FROM data_inconsistencies
                GROUP BY status
                """
            )
        return {r["status"]: r["cnt"] for r in rows}

    async def count_by_type(self, status: str = "open") -> dict[str, int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT inconsistency_type, COUNT(*) AS cnt
                FROM data_inconsistencies
                WHERE status = $1
                GROUP BY inconsistency_type
                """,
                status,
            )
        return {r["inconsistency_type"]: r["cnt"] for r in rows}

    async def update_status(
        self,
        inconsistency_id: int,
        status: str,
        resolved_by: str | None = None,
        resolution_note: str | None = None,
    ) -> None:
        resolved_at_expr = "NOW()" if status == "resolved" else "NULL"
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE data_inconsistencies
                SET status          = $2,
                    resolved_by     = $3,
                    resolution_note = $4,
                    resolved_at     = {resolved_at_expr}
                WHERE id = $1
                """,
                inconsistency_id,
                status,
                resolved_by,
                resolution_note,
            )

    async def mark_resolved_by_url(
        self,
        resource_url: str,
        inconsistency_type: str,
        resolution_note: str,
    ) -> int:
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE data_inconsistencies
                SET status          = 'resolved',
                    resolved_at     = NOW(),
                    resolution_note = $3
                WHERE resource_url = $1
                  AND inconsistency_type = $2
                  AND status = 'open'
                """,
                resource_url,
                inconsistency_type,
                resolution_note,
            )
        # asyncpg retorna "UPDATE N" como string
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    async def get_summary(self) -> InconsistencySummary:
        async with self._pool.acquire() as conn:
            # Totais por status
            status_rows = await conn.fetch(
                """
                SELECT status, COUNT(*) AS cnt
                FROM data_inconsistencies
                GROUP BY status
                """
            )
            status_counts = {r["status"]: r["cnt"] for r in status_rows}

            # Por severidade (apenas open)
            severity_rows = await conn.fetch(
                """
                SELECT severity, COUNT(*) AS cnt
                FROM data_inconsistencies
                WHERE status = 'open'
                GROUP BY severity
                """
            )

            # Por tipo (apenas open)
            type_rows = await conn.fetch(
                """
                SELECT inconsistency_type, COUNT(*) AS cnt
                FROM data_inconsistencies
                WHERE status = 'open'
                GROUP BY inconsistency_type
                """
            )

            # Por resource_type (apenas open)
            resource_rows = await conn.fetch(
                """
                SELECT resource_type, COUNT(*) AS cnt
                FROM data_inconsistencies
                WHERE status = 'open'
                GROUP BY resource_type
                """
            )

            # Mais antiga em aberto
            oldest_open = await conn.fetchval(
                """
                SELECT MIN(detected_at)
                FROM data_inconsistencies
                WHERE status = 'open'
                """
            )

            # Última verificação (qualquer status)
            last_check = await conn.fetchval(
                """
                SELECT MAX(last_checked_at)
                FROM data_inconsistencies
                """
            )

        def _ensure_tz(dt):  # type: ignore[no-untyped-def]
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt

        return InconsistencySummary(
            total_open=status_counts.get("open", 0),
            total_acknowledged=status_counts.get("acknowledged", 0),
            total_resolved=status_counts.get("resolved", 0),
            total_ignored=status_counts.get("ignored", 0),
            by_severity={r["severity"]: r["cnt"] for r in severity_rows},
            by_type={r["inconsistency_type"]: r["cnt"] for r in type_rows},
            by_resource_type={r["resource_type"]: r["cnt"] for r in resource_rows},
            oldest_open=_ensure_tz(oldest_open),
            last_check=_ensure_tz(last_check),
        )
