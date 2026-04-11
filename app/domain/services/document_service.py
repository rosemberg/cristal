"""Application service: DocumentService — implements DocumentUseCase."""

from __future__ import annotations

from app.domain.entities.document import Document
from app.domain.entities.document_table import DocumentTable
from app.domain.ports.inbound.document_use_case import DocumentUseCase
from app.domain.ports.outbound.document_repository import DocumentRepository


class DocumentService(DocumentUseCase):
    """Thin orchestration layer over DocumentRepository."""

    def __init__(self, document_repo: DocumentRepository) -> None:
        self._repo = document_repo

    async def list_documents(
        self,
        category: str | None = None,
        doc_type: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> list[Document]:
        return await self._repo.list_documents(
            category=category, doc_type=doc_type, page=page, size=size
        )

    async def get(self, document_url: str) -> Document | None:
        return await self._repo.find_by_url(document_url)

    async def get_content(self, document_url: str) -> str | None:
        chunks = await self._repo.get_chunks(document_url)
        if not chunks:
            return None
        return "\n\n".join(c.text for c in chunks)

    async def get_tables(self, document_url: str) -> list[DocumentTable]:
        return await self._repo.get_tables(document_url)
