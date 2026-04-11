"""Output port: DocumentProcessGateway ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.ports.outbound.document_repository import ProcessedDocument


class DocumentProcessGateway(ABC):
    """Converts raw document bytes into structured ProcessedDocument."""

    @abstractmethod
    async def process(
        self, url: str, content: bytes, doc_type: str
    ) -> ProcessedDocument:
        """Process document bytes and return extracted content.

        Args:
            url: The document URL (used as identifier in chunks/tables).
            content: Raw document bytes (PDF, CSV, or XLSX).
            doc_type: One of "pdf", "csv", "xlsx".

        Returns:
            ProcessedDocument with text, chunks, and tables.

        Raises:
            ValueError: If doc_type is not supported.
            DocumentProcessingError: If the document cannot be parsed.
        """
        ...


class DocumentProcessingError(Exception):
    """Raised when a document cannot be parsed."""
