"""DocumentProcessAdapter — wraps DocumentProcessor behind DocumentProcessGateway.

Corrige a Inconsistencia #1 do Pipeline V1: o DocumentIngestionService precisava
receber um port abstrato (DocumentProcessGateway), não a classe concreta
(DocumentProcessor). Este adapter fecha essa lacuna sem alterar o processor.
"""

from __future__ import annotations

from app.adapters.outbound.document_processor.document_processor import (
    DocumentProcessor,
)
from app.domain.ports.outbound.document_process_gateway import DocumentProcessGateway
from app.domain.ports.outbound.document_repository import ProcessedDocument


class DocumentProcessAdapter(DocumentProcessGateway):
    """Adapter that delegates to the concrete DocumentProcessor."""

    def __init__(self, processor: DocumentProcessor) -> None:
        self._processor = processor

    async def process(
        self, url: str, content: bytes, doc_type: str
    ) -> ProcessedDocument:
        """Delegate processing to the wrapped DocumentProcessor.

        Args:
            url: Document URL used as identifier in chunks/tables.
            content: Raw document bytes (PDF, CSV, or XLSX).
            doc_type: One of ``"pdf"``, ``"csv"``, ``"xlsx"``.

        Returns:
            ProcessedDocument with text, chunks, and tables.

        Raises:
            ValueError: If *doc_type* is not supported.
            DocumentProcessingError: If the document cannot be parsed.
        """
        return await self._processor.process(url, content, doc_type)
