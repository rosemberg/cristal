"""Value object: InconsistencySummary — resumo para o dashboard admin."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class InconsistencySummary:
    """Resumo agregado das inconsistências para o painel de administração."""

    total_open: int
    total_acknowledged: int
    total_resolved: int
    total_ignored: int
    by_severity: dict[str, int]       # {critical: N, warning: N, info: N}
    by_type: dict[str, int]           # {broken_link: N, document_not_found: N, ...}
    by_resource_type: dict[str, int]  # {page: N, document: N, link: N, chunk: N}
    oldest_open: datetime | None
    last_check: datetime | None
