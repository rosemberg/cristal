"""Value objects: IngestionStats e IngestionStatus."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IngestionStats:
    """Resultado de uma execução do pipeline de ingestão."""

    total: int
    processed: int
    errors: int
    skipped: int
    duration_seconds: float
    inconsistencies_found: int


@dataclass(frozen=True)
class IngestionStatus:
    """Estado atual do pipeline (snapshot dos contadores)."""

    pending: int
    processing: int
    done: int
    error: int
    total_chunks: int
    total_tables: int
    open_inconsistencies: int
