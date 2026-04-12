"""Domain entity: DocumentChunk."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DocumentChunk:
    id: int
    document_url: str
    chunk_index: int
    text: str
    token_count: int
    section_title: str | None = None
    page_number: int | None = None
    version: int = 1              # 1 = TextChunker (legado), 2 = SemanticChunker
    has_table: bool = False       # chunk contém tabela inline
    parent_chunk_id: int | None = None  # ID do chunk pai (para sub-chunks)
