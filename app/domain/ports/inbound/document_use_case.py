"""Input port: DocumentUseCase ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.document import Document
from app.domain.entities.document_table import DocumentTable


class DocumentUseCase(ABC):
    @abstractmethod
    async def list_documents(
        self,
        category: str | None = None,
        doc_type: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> list[Document]: ...

    @abstractmethod
    async def get(self, document_url: str) -> Document | None: ...

    @abstractmethod
    async def get_content(self, document_url: str) -> str | None: ...

    @abstractmethod
    async def get_tables(self, document_url: str) -> list[DocumentTable]: ...
