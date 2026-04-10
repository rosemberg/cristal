"""Value objects: PageMatch, ChunkMatch, HybridSearchResult."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document_table import DocumentTable
from app.domain.entities.page import Page


@dataclass(frozen=True)
class PageMatch:
    page: Page
    score: float
    highlight: str | None = None


@dataclass(frozen=True)
class ChunkMatch:
    chunk: DocumentChunk
    document_title: str
    document_url: str
    score: float


@dataclass(frozen=True)
class HybridSearchResult:
    pages: list[PageMatch]
    chunks: list[ChunkMatch]
    tables: list[DocumentTable]
