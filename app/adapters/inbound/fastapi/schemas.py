"""Pydantic request/response schemas para a API FastAPI."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: UUID | None = None
    history: list[dict[str, object]] | None = None


class CitationOut(BaseModel):
    document_title: str
    document_url: str
    snippet: str
    page_number: int | None = None


class TableDataOut(BaseModel):
    headers: list[str]
    rows: list[list[str]]
    source_document: str
    title: str | None = None
    page_number: int | None = None


class ChatResponse(BaseModel):
    text: str
    sources: list[CitationOut]
    tables: list[TableDataOut]
    suggestions: list[str] = Field(default_factory=list)


class SuggestResponse(BaseModel):
    suggestions: list[str]


class CategoryItem(BaseModel):
    name: str
    page_count: int = 0


class CategoriesResponse(BaseModel):
    categories: list[CategoryItem]


class TransparencyMapItem(BaseModel):
    category: str
    page_count: int = 0


class TransparencyMapResponse(BaseModel):
    categories: list[TransparencyMapItem]
    totals: dict[str, object]


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------


class DocumentOut(BaseModel):
    id: int
    page_url: str
    document_url: str
    type: str
    is_processed: bool
    title: str | None = None
    num_pages: int | None = None


class DocumentListResponse(BaseModel):
    documents: list[DocumentOut]


class DocumentContentResponse(BaseModel):
    content: str | None = None


class TableOut(BaseModel):
    id: int
    table_index: int
    headers: list[str]
    rows: list[list[str]]
    caption: str | None = None
    page_number: int | None = None


class DocumentTablesResponse(BaseModel):
    tables: list[TableOut]


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


class SessionCreateRequest(BaseModel):
    title: str | None = None


class SessionOut(BaseModel):
    id: UUID
    created_at: datetime
    last_active: datetime
    title: str | None = None


class SessionListResponse(BaseModel):
    sessions: list[SessionOut]


class MessageOut(BaseModel):
    role: str
    content: str
    sources: list[CitationOut]
    tables: list[TableDataOut]


class MessagesResponse(BaseModel):
    messages: list[MessageOut]


# ---------------------------------------------------------------------------
# Analytics (Admin)
# ---------------------------------------------------------------------------


class DailyStatItem(BaseModel):
    date: str
    query_count: int
    avg_response_time_ms: float


class MetricsOut(BaseModel):
    total_queries: int
    avg_response_time_ms: float
    positive_feedback: int
    negative_feedback: int
    satisfaction_rate: float


class AnalyticsResponse(BaseModel):
    metrics: MetricsOut
    daily_stats: list[DailyStatItem]
    days: int


# ---------------------------------------------------------------------------
# Ingestion (Admin — Etapa 7)
# ---------------------------------------------------------------------------


class IngestionStatsOut(BaseModel):
    total: int
    processed: int
    errors: int
    skipped: int
    duration_seconds: float
    inconsistencies_found: int


class IngestionStatusOut(BaseModel):
    pending: int
    processing: int
    done: int
    error: int
    total_chunks: int
    total_tables: int
    open_inconsistencies: int


class HealthCheckReportOut(BaseModel):
    total_checked: int
    healthy: int
    issues_found: int
    new_inconsistencies: int
    updated_inconsistencies: int
    auto_resolved: int
    duration_seconds: float
    by_type: dict[str, int]


class DataInconsistencyOut(BaseModel):
    id: int | None
    resource_type: str
    severity: str
    inconsistency_type: str
    resource_url: str
    resource_title: str | None
    parent_page_url: str | None
    detail: str
    http_status: int | None
    error_message: str | None
    detected_at: datetime
    detected_by: str
    status: str
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_note: str | None
    retry_count: int
    last_checked_at: datetime


class InconsistencySummaryOut(BaseModel):
    total: int
    by_severity: dict[str, int]
    by_type: dict[str, int]
    by_resource_type: dict[str, int]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class PoolStatus(BaseModel):
    connected: bool
    pool_min: int
    pool_max: int
    pool_size: int | None = None


class HealthResponse(BaseModel):
    status: str  # healthy | degraded | unhealthy
    version: str
    database: PoolStatus
    stats: dict[str, object]
