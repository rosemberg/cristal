"""Value objects: DataInconsistency e HealthCheckReport."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class DataInconsistency:
    """Registro de uma inconsistência detectada no pipeline ou health check."""

    id: int | None
    resource_type: str      # 'page' | 'document' | 'link' | 'chunk'
    severity: str           # 'critical' | 'warning' | 'info'
    inconsistency_type: str
    resource_url: str
    resource_title: str | None
    parent_page_url: str | None
    detail: str
    http_status: int | None
    error_message: str | None
    detected_at: datetime
    detected_by: str        # 'ingestion_pipeline' | 'health_check' | 'crawler' | 'manual'
    status: str             # 'open' | 'acknowledged' | 'resolved' | 'ignored'
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_note: str | None
    retry_count: int
    last_checked_at: datetime


@dataclass(frozen=True)
class HealthCheckReport:
    """Resultado de uma execução do DataHealthCheckService."""

    total_checked: int
    healthy: int
    issues_found: int
    new_inconsistencies: int
    updated_inconsistencies: int
    auto_resolved: int
    duration_seconds: float
    by_type: dict[str, int]  # contagem por inconsistency_type
