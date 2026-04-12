"""PDF processor using PyMuPDF (fitz).

Extracts text page-by-page and attempts table detection.
Tables are extracted on a best-effort basis; text extraction always succeeds
for valid PDFs.
"""

from __future__ import annotations

import io
import logging

import fitz
import pytesseract
from PIL import Image

from app.adapters.outbound.document_processor.semantic_chunker import SemanticChunker
from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document_table import DocumentTable
from app.domain.ports.outbound.document_process_gateway import DocumentProcessingError
from app.domain.ports.outbound.document_repository import ProcessedDocument

logger = logging.getLogger(__name__)


class PdfProcessor:
    """Processes PDF bytes into a ProcessedDocument."""

    def __init__(self, chunker: SemanticChunker) -> None:
        self._chunker = chunker

    @staticmethod
    def _ocr_page(page: fitz.Page) -> str:
        """Fallback OCR para páginas escaneadas (sem camada de texto)."""
        try:
            pix = page.get_pixmap(dpi=300)
            img = Image.open(io.BytesIO(pix.tobytes("png")))
            return pytesseract.image_to_string(img, lang="por")
        except Exception as exc:  # noqa: BLE001
            logger.debug("OCR falhou na página: %s", exc)
            return ""

    def process(self, content: bytes, document_url: str) -> ProcessedDocument:
        """Extract text, chunks, and tables from PDF bytes.

        Args:
            content: Raw PDF bytes.
            document_url: URL used as the document identifier.

        Returns:
            ProcessedDocument with text, chunks, and any detected tables.

        Raises:
            DocumentProcessingError: If *content* is not a valid PDF.
        """
        try:
            doc = fitz.open(stream=content, filetype="pdf")
        except Exception as exc:
            raise DocumentProcessingError(
                f"Não foi possível abrir o PDF: {exc}"
            ) from exc

        text_parts: list[str] = []
        all_chunks: list[DocumentChunk] = []
        all_tables: list[DocumentTable] = []

        for page_num, page in enumerate(doc, start=1):
            page_text: str = page.get_text()
            if not page_text.strip():
                page_text = self._ocr_page(page)
            if page_text.strip():
                text_parts.append(page_text)
                page_chunks = self._chunker.chunk_plain_text(
                    text=page_text,
                    document_url=document_url,
                    page_number=page_num,
                    start_index=len(all_chunks),
                )
                all_chunks.extend(page_chunks)

            # Table extraction — best-effort (not all PDFs have detectable tables)
            try:
                for table in page.find_tables():
                    extracted: list[list[str | None]] = table.extract()
                    if not extracted:
                        continue
                    headers = [str(cell) if cell is not None else "" for cell in extracted[0]]
                    rows = [
                        [str(cell) if cell is not None else "" for cell in row]
                        for row in extracted[1:]
                    ]
                    all_tables.append(
                        DocumentTable(
                            id=0,  # Sentinel: DB assigns real id
                            document_url=document_url,
                            table_index=len(all_tables),
                            headers=headers,
                            rows=rows,
                            page_number=page_num,
                            num_rows=len(rows),
                            num_cols=len(headers),
                        )
                    )
            except Exception as table_exc:  # noqa: BLE001
                logger.debug("Table extraction skipped on page %d: %s", page_num, table_exc)

        metadata: dict[str, str] = doc.metadata or {}
        raw_title = metadata.get("title", "")
        title: str | None = raw_title.strip() if raw_title and raw_title.strip() else None

        return ProcessedDocument(
            document_url=document_url,
            text="\n\n".join(text_parts),
            chunks=all_chunks,
            tables=all_tables,
            num_pages=doc.page_count,
            title=title,
        )
