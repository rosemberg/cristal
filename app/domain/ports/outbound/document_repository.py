"""Output port: DocumentRepository ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document import Document
from app.domain.entities.document_table import DocumentTable


@dataclass
class DocumentCheckInfo:
    """Informação mínima de um documento concluído para verificação de saúde."""

    url: str
    title: str | None
    page_url: str
    stored_content_length: int | None


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

    @abstractmethod
    async def list_pending(self, limit: int = 0) -> list[Document]:
        """Lista documentos pendentes. limit=0 retorna todos."""
        ...

    @abstractmethod
    async def list_errors(self) -> list[Document]: ...

    @abstractmethod
    async def reset_stuck_processing(self, stuck_minutes: int = 30) -> int:
        """Reseta documentos presos em 'processing' para 'pending'.

        Args:
            stuck_minutes: threshold de tempo; 0 reseta todos em 'processing'.

        Returns:
            Número de documentos resetados.
        """
        ...

    @abstractmethod
    async def update_status(
        self, document_url: str, status: str, error: str | None = None
    ) -> None: ...

    @abstractmethod
    async def save_content_atomic(
        self, document_url: str, content: ProcessedDocument
    ) -> None: ...
    # Salva chunks e tabelas em transação única — rollback em caso de falha

    @abstractmethod
    async def count_by_status(self) -> dict[str, int]: ...
    # {pending: N, processing: N, done: N, error: N}

    @abstractmethod
    async def count_chunks(self) -> int: ...

    @abstractmethod
    async def count_tables(self) -> int: ...

    @abstractmethod
    async def list_done(self) -> list[DocumentCheckInfo]: ...
    # Retorna documentos com processing_status='done' para health check
