"""Unit tests — DocumentProcessAdapter (Etapa 2).

Verifica que o adapter delega corretamente ao DocumentProcessGateway
subjacente e retorna o ProcessedDocument recebido.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.adapters.outbound.document_processor.document_process_adapter import (
    DocumentProcessAdapter,
)
from app.domain.ports.outbound.document_process_gateway import DocumentProcessGateway
from app.domain.ports.outbound.document_repository import ProcessedDocument

PDF_URL = "https://www.tre-pi.jus.br/relatorio.pdf"
PDF_BYTES = b"%PDF-1.4 fake"
CSV_URL = "https://www.tre-pi.jus.br/dados.csv"
CSV_BYTES = b"col1,col2\n1,2"


def make_processed_document(url: str = PDF_URL) -> ProcessedDocument:
    return ProcessedDocument(
        document_url=url,
        text="Conteúdo extraído",
        chunks=[],
        tables=[],
        num_pages=1,
        title="Documento de Teste",
    )


# ─── Contrato do port ─────────────────────────────────────────────────────────


class TestDocumentProcessAdapterIsGateway:
    def test_adapter_implements_document_process_gateway(self) -> None:
        inner = AsyncMock(spec=DocumentProcessGateway)
        adapter = DocumentProcessAdapter(inner)
        assert isinstance(adapter, DocumentProcessGateway)


# ─── Delegação ────────────────────────────────────────────────────────────────


class TestDocumentProcessAdapterDelegation:
    async def test_process_calls_inner_process(self) -> None:
        expected = make_processed_document()
        inner = AsyncMock(spec=DocumentProcessGateway)
        inner.process = AsyncMock(return_value=expected)

        adapter = DocumentProcessAdapter(inner)
        await adapter.process(PDF_URL, PDF_BYTES, "pdf")

        inner.process.assert_called_once_with(PDF_URL, PDF_BYTES, "pdf")

    async def test_process_returns_result_from_inner(self) -> None:
        expected = make_processed_document()
        inner = AsyncMock(spec=DocumentProcessGateway)
        inner.process = AsyncMock(return_value=expected)

        adapter = DocumentProcessAdapter(inner)
        result = await adapter.process(PDF_URL, PDF_BYTES, "pdf")

        assert result is expected

    async def test_process_forwards_csv_doc_type(self) -> None:
        expected = make_processed_document(url=CSV_URL)
        inner = AsyncMock(spec=DocumentProcessGateway)
        inner.process = AsyncMock(return_value=expected)

        adapter = DocumentProcessAdapter(inner)
        result = await adapter.process(CSV_URL, CSV_BYTES, "csv")

        inner.process.assert_called_once_with(CSV_URL, CSV_BYTES, "csv")
        assert result is expected

    async def test_process_propagates_value_error(self) -> None:
        inner = AsyncMock(spec=DocumentProcessGateway)
        inner.process = AsyncMock(
            side_effect=ValueError("Unsupported document type: 'docx'")
        )

        adapter = DocumentProcessAdapter(inner)
        with pytest.raises(ValueError, match="Unsupported"):
            await adapter.process(PDF_URL, b"", "docx")

    async def test_process_propagates_processing_error(self) -> None:
        from app.domain.ports.outbound.document_process_gateway import (
            DocumentProcessingError,
        )

        inner = AsyncMock(spec=DocumentProcessGateway)
        inner.process = AsyncMock(
            side_effect=DocumentProcessingError("parse failed")
        )

        adapter = DocumentProcessAdapter(inner)
        with pytest.raises(DocumentProcessingError):
            await adapter.process(PDF_URL, b"corrupted", "pdf")


# ─── Construção ───────────────────────────────────────────────────────────────


class TestDocumentProcessAdapterConstruction:
    def test_adapter_stores_inner_processor(self) -> None:
        inner = AsyncMock(spec=DocumentProcessGateway)
        adapter = DocumentProcessAdapter(inner)
        assert adapter._processor is inner
