"""Domain entity: DocumentTable."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocumentTable:
    id: int
    document_url: str
    table_index: int
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)
    page_number: int | None = None
    caption: str | None = None
    num_rows: int | None = None
    num_cols: int | None = None
