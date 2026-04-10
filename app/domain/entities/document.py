"""Domain entity: Document."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document_table import DocumentTable


@dataclass
class Document:
    id: int
    page_url: str
    document_url: str
    type: str  # pdf | csv | xlsx
    is_processed: bool = False
    title: str | None = None
    num_pages: int | None = None
    chunks: list[DocumentChunk] = field(default_factory=list)
    tables: list[DocumentTable] = field(default_factory=list)
