"""Value objects: PageMatch, ChunkMatch, SemanticMatch, HybridSearchResult."""

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
class SemanticMatch:
    """Resultado de busca semântica via cosine similarity em embeddings.

    Unifica chunks e pages num único value object para o RRF do HybridSearchService.
    """

    source_id: int
    source_type: str          # 'chunk' | 'page' | 'table'
    similarity: float         # 1 - cosine_distance (0..1, maior = mais similar)
    document_url: str | None = None
    document_title: str | None = None
    chunk: DocumentChunk | None = None
    page: Page | None = None


@dataclass(frozen=True)
class HybridSearchResult:
    pages: list[PageMatch]
    chunks: list[ChunkMatch]
    tables: list[DocumentTable]
