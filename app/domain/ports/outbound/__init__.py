"""Outbound ports (ABCs) — driven side of the hexagon."""

from app.domain.ports.outbound.analytics_repository import AnalyticsRepository
from app.domain.ports.outbound.content_fetch_gateway import (
    ContentFetchGateway,
    FetchResult,
)
from app.domain.ports.outbound.document_repository import (
    DocumentRepository,
    ProcessedDocument,
)
from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.ports.outbound.search_repository import SearchRepository
from app.domain.ports.outbound.session_repository import SessionRepository

__all__ = [
    "AnalyticsRepository",
    "ContentFetchGateway",
    "DocumentRepository",
    "FetchResult",
    "LLMGateway",
    "ProcessedDocument",
    "SearchRepository",
    "SessionRepository",
]
