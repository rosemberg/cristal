"""CSV/XLSX processor using pandas.

Converts tabular data into DocumentTable entities and generates a text
representation of the content for RAG chunking.

Supported types: "csv", "xlsx".
"""

from __future__ import annotations

import io

import pandas as pd

from app.adapters.outbound.document_processor.chunker import TextChunker
from app.domain.entities.document_table import DocumentTable
from app.domain.ports.outbound.document_repository import ProcessedDocument

# Maximum data rows included in the text representation (avoids huge chunks)
_MAX_TEXT_ROWS = 100


class CsvProcessor:
    """Processes CSV/XLSX bytes into a ProcessedDocument."""

    def __init__(self, chunker: TextChunker) -> None:
        self._chunker = chunker

    def process(
        self, content: bytes, document_url: str, doc_type: str = "csv"
    ) -> ProcessedDocument:
        """Extract tables and generate chunks from CSV or XLSX bytes.

        Args:
            content: Raw file bytes.
            document_url: URL used as the document identifier.
            doc_type: ``"csv"`` or ``"xlsx"``.

        Returns:
            ProcessedDocument with tables and text/chunks derived from the data.

        Raises:
            ValueError: If *doc_type* is not "csv" or "xlsx".
            DocumentProcessingError: If pandas cannot parse the content.
        """
        sheets = self._load_sheets(content, doc_type)

        all_tables: list[DocumentTable] = []
        text_parts: list[str] = []

        for sheet_name, df in sheets.items():
            headers = [str(col) for col in df.columns.tolist()]
            rows = [
                [str(cell) if pd.notna(cell) else "" for cell in row]
                for row in df.values.tolist()
            ]

            all_tables.append(
                DocumentTable(
                    id=0,  # Sentinel: DB assigns real id
                    document_url=document_url,
                    table_index=len(all_tables),
                    headers=headers,
                    rows=rows,
                    caption=sheet_name,
                    num_rows=len(rows),
                    num_cols=len(headers),
                )
            )

            # Build text representation (header + first N rows)
            text_parts.append(f"Tabela: {sheet_name}")
            text_parts.append(" | ".join(headers))
            for row in rows[:_MAX_TEXT_ROWS]:
                text_parts.append(" | ".join(row))

        full_text = "\n".join(text_parts)
        chunks = self._chunker.chunk(text=full_text, document_url=document_url)

        return ProcessedDocument(
            document_url=document_url,
            text=full_text,
            chunks=chunks,
            tables=all_tables,
            num_pages=None,
            title=None,
        )

    # ------------------------------------------------------------------

    def _load_sheets(
        self, content: bytes, doc_type: str
    ) -> dict[str, pd.DataFrame]:
        buf = io.BytesIO(content)
        if doc_type == "xlsx":
            sheets: dict[str, pd.DataFrame] = pd.read_excel(
                buf, sheet_name=None, engine="openpyxl"
            )
            return sheets
        if doc_type == "csv":
            return {"Sheet1": pd.read_csv(buf)}
        raise ValueError(f"Unsupported tabular format: {doc_type!r}")
