"""Value objects: ChatMessage, Citation, TableData."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Citation:
    document_title: str
    document_url: str
    snippet: str
    page_number: int | None = None


@dataclass(frozen=True)
class TableData:
    headers: list[str]
    rows: list[list[str]]
    source_document: str
    title: str | None = None
    page_number: int | None = None


@dataclass(frozen=True)
class ChatMessage:
    role: str  # user | assistant
    content: str
    sources: list[Citation]
    tables: list[TableData]
