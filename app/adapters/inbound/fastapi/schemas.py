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
