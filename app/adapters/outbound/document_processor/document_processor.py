"""DocumentProcessor — concrete implementation of DocumentProcessGateway.

Routes document bytes to the appropriate sub-processor (PDF or CSV/XLSX)
and returns a unified ProcessedDocument.
"""

from __future__ import annotations

from app.adapters.outbound.document_processor.chunker import TextChunker
from app.adapters.outbound.document_processor.csv_processor import CsvProcessor
from app.adapters.outbound.document_processor.pdf_processor import PdfProcessor
from app.domain.ports.outbound.document_process_gateway import DocumentProcessGateway
from app.domain.ports.outbound.document_repository import ProcessedDocument

_TABULAR_TYPES = frozenset({"csv", "xlsx"})


class DocumentProcessor(DocumentProcessGateway):
    """Dispatches processing to PdfProcessor or CsvProcessor."""

    def __init__(self, chunk_size: int = 500, overlap: int = 50) -> None:
        chunker = TextChunker(chunk_size=chunk_size, overlap=overlap)
        self._pdf = PdfProcessor(chunker)
        self._csv = CsvProcessor(chunker)

    async def process(
        self, url: str, content: bytes, doc_type: str
    ) -> ProcessedDocument:
        """Process document bytes and return structured content.

        Args:
            url: The document URL (used as identifier in chunks/tables).
            content: Raw document bytes.
            doc_type: One of ``"pdf"``, ``"csv"``, ``"xlsx"``.

        Returns:
            ProcessedDocument with text, chunks, and tables.

        Raises:
            ValueError: If *doc_type* is not supported.
            DocumentProcessingError: If the document cannot be parsed.
        """
        if doc_type == "pdf":
            return self._pdf.process(content, url)
        if doc_type in _TABULAR_TYPES:
            return self._csv.process(content, url, doc_type=doc_type)
        raise ValueError(
            f"Unsupported document type: {doc_type!r}. "
            f"Supported: 'pdf', 'csv', 'xlsx'."
        )
