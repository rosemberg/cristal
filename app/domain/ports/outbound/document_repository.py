"""Output port: DocumentRepository ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document import Document
from app.domain.entities.document_table import DocumentTable


@dataclass
class ProcessedDocument:
    """Value object produced by the document processor (Etapa 7)."""

    document_url: str
    text: str
    chunks: list[DocumentChunk] = field(default_factory=list)
    tables: list[DocumentTable] = field(default_factory=list)
    num_pages: int | None = None
    title: str | None = None


class DocumentRepository(ABC):
    @abstractmethod
    async def find_by_url(self, url: str) -> Document | None: ...

    @abstractmethod
    async def list_documents(
        self,
        category: str | None = None,
        doc_type: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> list[Document]: ...

    @abstractmethod
    async def get_chunks(self, document_url: str) -> list[DocumentChunk]: ...

    @abstractmethod
    async def get_tables(self, document_url: str) -> list[DocumentTable]: ...

    @abstractmethod
    async def save_content(
        self, document_url: str, content: ProcessedDocument
    ) -> None: ...
