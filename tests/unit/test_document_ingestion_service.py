"""Unit tests — DocumentIngestionService (Etapa 4).

TDD RED → GREEN para o orquestrador do pipeline de ingestão.

Cobre:
- Fluxo feliz: pending → processing → done
- Erros de download: HTTP 4xx/5xx, exceções de rede, tamanho
- Erros de processamento: DocumentProcessingError, chunks vazios
- Classificação de inconsistências
- Concorrência via Semaphore
- ingest_single, reprocess_errors, get_status
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, call, patch

import pytest

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document import Document
from app.domain.ports.inbound.document_ingestion_use_case import DocumentIngestionUseCase
from app.domain.ports.outbound.document_download_gateway import (
    DocumentDownloadGateway,
    DownloadError,
    DownloadResult,
)
from app.domain.ports.outbound.document_process_gateway import (
    DocumentProcessGateway,
    DocumentProcessingError,
)
from app.domain.ports.outbound.document_repository import DocumentRepository, ProcessedDocument
from app.domain.ports.outbound.inconsistency_repository import InconsistencyRepository
from app.domain.services.document_ingestion_service import DocumentIngestionService
from app.domain.value_objects.data_inconsistency import DataInconsistency
from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus

# ── Constantes de teste ───────────────────────────────────────────────────────

DOC_URL = "https://www.tre-pi.jus.br/doc/relatorio.pdf"
DOC_URL_2 = "https://www.tre-pi.jus.br/doc/planilha.csv"
PAGE_URL = "https://www.tre-pi.jus.br/transparencia"
PDF_BYTES = b"%PDF-1.4 fake content"


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_document(
    url: str = DOC_URL,
    doc_type: str = "pdf",
    title: str = "Relatório de Teste",
    page_url: str = PAGE_URL,
) -> Document:
    return Document(id=1, page_url=page_url, document_url=url, type=doc_type, title=title)


def make_download_result(
    status_code: int = 200,
    content: bytes = PDF_BYTES,
    content_type: str = "application/pdf",
) -> DownloadResult:
    return DownloadResult(
        content=content,
        content_type=content_type,
        size_bytes=len(content),
        status_code=status_code,
    )


def make_chunk(url: str = DOC_URL) -> DocumentChunk:
    return DocumentChunk(
        id=None,  # type: ignore[arg-type]
        document_url=url,
        chunk_index=0,
        text="Conteúdo do chunk de teste.",
        token_count=10,
    )


def make_processed_document(url: str = DOC_URL, with_chunks: bool = True) -> ProcessedDocument:
    chunks = [make_chunk(url)] if with_chunks else []
    return ProcessedDocument(
        document_url=url,
        text="Texto extraído do documento." if with_chunks else "",
        chunks=chunks,
        tables=[],
        num_pages=1,
        title="Relatório de Teste",
    )


def make_service(
    doc_repo: DocumentRepository | None = None,
    downloader: DocumentDownloadGateway | None = None,
    processor: DocumentProcessGateway | None = None,
    inconsistency_repo: InconsistencyRepository | None = None,
    concurrency: int = 3,
) -> DocumentIngestionService:
    return DocumentIngestionService(
        doc_repo=doc_repo or AsyncMock(spec=DocumentRepository),
        downloader=downloader or AsyncMock(spec=DocumentDownloadGateway),
        processor=processor or AsyncMock(spec=DocumentProcessGateway),
        inconsistency_repo=inconsistency_repo or AsyncMock(spec=InconsistencyRepository),
        concurrency=concurrency,
    )


def make_size_limit_error() -> DownloadError:
    err = DownloadError("Document exceeds size limit: 55.0MB > 50MB")
    err.is_size_limit = True
    return err


# ── Contrato do port ──────────────────────────────────────────────────────────


class TestDocumentIngestionServiceIsUseCase:
    def test_service_implements_document_ingestion_use_case(self) -> None:
        service = make_service()
        assert isinstance(service, DocumentIngestionUseCase)


# ── ingest_pending — fluxo feliz ──────────────────────────────────────────────


class TestIngestPendingHappyPath:
    async def test_no_pending_documents_returns_zero_stats(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[])

        service = make_service(doc_repo=doc_repo)
        stats = await service.ingest_pending()

        assert stats.total == 0
        assert stats.processed == 0
        assert stats.errors == 0
        assert stats.inconsistencies_found == 0

    async def test_status_set_to_processing_before_download(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        call_order: list[str] = []

        async def status_tracker(url: str, status: str, error: str | None = None) -> None:
            call_order.append(status)

        async def download_tracker(url: str) -> DownloadResult:
            call_order.append("download")
            return make_download_result()

        doc_repo.update_status = AsyncMock(side_effect=status_tracker)
        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(side_effect=download_tracker)
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        await service.ingest_pending()

        assert call_order[0] == "processing"

    async def test_status_set_to_done_after_save(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])
        doc_repo.update_status = AsyncMock()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        await service.ingest_pending()

        status_calls = [c.args[1] for c in doc_repo.update_status.call_args_list]
        assert "done" in status_calls

    async def test_save_content_atomic_is_called(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])
        processed = make_processed_document()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=processed)

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        await service.ingest_pending()

        doc_repo.save_content_atomic.assert_called_once_with(DOC_URL, processed)

    async def test_stats_duration_is_positive(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[])

        service = make_service(doc_repo=doc_repo)
        stats = await service.ingest_pending()

        assert stats.duration_seconds >= 0.0

    async def test_two_documents_both_processed(self) -> None:
        docs = [make_document(url=DOC_URL), make_document(url=DOC_URL_2, doc_type="csv")]
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=docs)

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        stats = await service.ingest_pending()

        assert stats.total == 2
        assert stats.processed == 2
        assert stats.errors == 0


# ── ingest_pending — erros HTTP ───────────────────────────────────────────────


class TestIngestPendingHttpErrors:
    async def test_http_404_sets_status_to_error(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result(status_code=404))

        service = make_service(doc_repo=doc_repo, downloader=downloader)
        await service.ingest_pending()

        status_calls = [c.args[1] for c in doc_repo.update_status.call_args_list]
        assert "error" in status_calls

    async def test_http_404_creates_document_not_found_critical_inconsistency(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result(status_code=404))

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo, downloader=downloader, inconsistency_repo=inconsistency_repo
        )
        await service.ingest_pending()

        inconsistency_repo.upsert.assert_called_once()
        _, inconsistency_type, inconsistency = inconsistency_repo.upsert.call_args.args
        assert inconsistency_type == "document_not_found"
        assert inconsistency.severity == "critical"

    async def test_http_500_creates_document_not_found_warning_inconsistency(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result(status_code=500))

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo, downloader=downloader, inconsistency_repo=inconsistency_repo
        )
        await service.ingest_pending()

        inconsistency_repo.upsert.assert_called_once()
        _, inconsistency_type, inconsistency = inconsistency_repo.upsert.call_args.args
        assert inconsistency_type == "document_not_found"
        assert inconsistency.severity == "warning"

    async def test_http_404_increments_error_count_in_stats(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result(status_code=404))

        service = make_service(doc_repo=doc_repo, downloader=downloader)
        stats = await service.ingest_pending()

        assert stats.errors == 1
        assert stats.processed == 0
        assert stats.inconsistencies_found == 1


# ── ingest_pending — exceções de download ─────────────────────────────────────


class TestIngestPendingDownloadExceptions:
    async def test_size_limit_creates_oversized_info_inconsistency(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(side_effect=make_size_limit_error())

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo, downloader=downloader, inconsistency_repo=inconsistency_repo
        )
        await service.ingest_pending()

        inconsistency_repo.upsert.assert_called_once()
        _, inconsistency_type, inconsistency = inconsistency_repo.upsert.call_args.args
        assert inconsistency_type == "oversized"
        assert inconsistency.severity == "info"

    async def test_network_error_creates_document_not_found_warning(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        network_error = DownloadError("Timeout after 3 retries")
        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(side_effect=network_error)

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo, downloader=downloader, inconsistency_repo=inconsistency_repo
        )
        await service.ingest_pending()

        _, inconsistency_type, inconsistency = inconsistency_repo.upsert.call_args.args
        assert inconsistency_type == "document_not_found"
        assert inconsistency.severity == "warning"

    async def test_download_error_does_not_stop_remaining_batch(self) -> None:
        docs = [
            make_document(url=DOC_URL),
            make_document(url=DOC_URL_2, doc_type="csv"),
        ]
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=docs)

        async def download_side_effect(url: str) -> DownloadResult:
            if url == DOC_URL:
                raise DownloadError("connection failed")
            return make_download_result()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(side_effect=download_side_effect)
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document(url=DOC_URL_2))

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        stats = await service.ingest_pending()

        assert stats.total == 2
        assert stats.processed == 1
        assert stats.errors == 1


# ── ingest_pending — erros de processamento ──────────────────────────────────


class TestIngestPendingProcessingErrors:
    async def test_processing_error_sets_status_to_error(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(side_effect=DocumentProcessingError("parse error"))

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        await service.ingest_pending()

        status_calls = [c.args[1] for c in doc_repo.update_status.call_args_list]
        assert "error" in status_calls

    async def test_generic_processing_error_creates_document_corrupted_critical(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(side_effect=DocumentProcessingError("unexpected parse error"))

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo,
            downloader=downloader,
            processor=processor,
            inconsistency_repo=inconsistency_repo,
        )
        await service.ingest_pending()

        _, inconsistency_type, inconsistency = inconsistency_repo.upsert.call_args.args
        assert inconsistency_type == "document_corrupted"
        assert inconsistency.severity == "critical"

    async def test_encoding_error_creates_encoding_error_warning_inconsistency(self) -> None:
        doc = make_document(doc_type="csv")
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result(content_type="text/csv"))
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(
            side_effect=DocumentProcessingError("encoding error: invalid utf-8 sequence")
        )

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo,
            downloader=downloader,
            processor=processor,
            inconsistency_repo=inconsistency_repo,
        )
        await service.ingest_pending()

        _, inconsistency_type, inconsistency = inconsistency_repo.upsert.call_args.args
        assert inconsistency_type == "encoding_error"
        assert inconsistency.severity == "warning"

    async def test_pdf_no_text_creates_document_corrupted_warning(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(
            side_effect=DocumentProcessingError("PDF has no extractable text (scanned image)")
        )

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo,
            downloader=downloader,
            processor=processor,
            inconsistency_repo=inconsistency_repo,
        )
        await service.ingest_pending()

        _, inconsistency_type, inconsistency = inconsistency_repo.upsert.call_args.args
        assert inconsistency_type == "document_corrupted"
        assert inconsistency.severity == "warning"

    async def test_empty_chunks_creates_empty_content_critical_inconsistency(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document(with_chunks=False))

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo,
            downloader=downloader,
            processor=processor,
            inconsistency_repo=inconsistency_repo,
        )
        await service.ingest_pending()

        _, inconsistency_type, inconsistency = inconsistency_repo.upsert.call_args.args
        assert inconsistency_type == "empty_content"
        assert inconsistency.severity == "critical"

    async def test_unexpected_exception_creates_document_corrupted_critical(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(side_effect=RuntimeError("unexpected crash"))

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo,
            downloader=downloader,
            inconsistency_repo=inconsistency_repo,
        )
        await service.ingest_pending()

        _, inconsistency_type, inconsistency = inconsistency_repo.upsert.call_args.args
        assert inconsistency_type == "document_corrupted"
        assert inconsistency.severity == "critical"


# ── ingest_pending — campos da DataInconsistency ──────────────────────────────


class TestInconsistencyFields:
    async def test_inconsistency_has_correct_resource_fields(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result(status_code=404))

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        service = make_service(
            doc_repo=doc_repo, downloader=downloader, inconsistency_repo=inconsistency_repo
        )
        await service.ingest_pending()

        resource_url, _, inconsistency = inconsistency_repo.upsert.call_args.args
        assert resource_url == DOC_URL
        assert inconsistency.resource_url == DOC_URL
        assert inconsistency.resource_type == "document"
        assert inconsistency.detected_by == "ingestion_pipeline"
        assert inconsistency.status == "open"
        assert inconsistency.http_status == 404


# ── ingest_pending — concorrência ─────────────────────────────────────────────


class TestIngestPendingConcurrency:
    async def test_semaphore_limits_concurrent_executions(self) -> None:
        """Com concurrency=1, documentos devem ser processados sequencialmente."""
        active: list[int] = []
        max_active: list[int] = [0]
        current_active = 0

        docs = [make_document(url=DOC_URL), make_document(url=DOC_URL_2, doc_type="csv")]
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=docs)

        async def slow_download(url: str) -> DownloadResult:
            nonlocal current_active
            current_active += 1
            max_active[0] = max(max_active[0], current_active)
            await asyncio.sleep(0.01)
            current_active -= 1
            return make_download_result()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(side_effect=slow_download)
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(
            doc_repo=doc_repo, downloader=downloader, processor=processor, concurrency=1
        )
        await service.ingest_pending(concurrency=1)

        assert max_active[0] == 1

    async def test_concurrency_2_allows_parallel_downloads(self) -> None:
        """Com concurrency=2, dois documentos podem ser processados em paralelo."""
        max_active: list[int] = [0]
        current_active = 0

        docs = [
            make_document(url=DOC_URL),
            make_document(url=DOC_URL_2, doc_type="csv"),
        ]
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=docs)

        async def slow_download(url: str) -> DownloadResult:
            nonlocal current_active
            current_active += 1
            max_active[0] = max(max_active[0], current_active)
            await asyncio.sleep(0.05)
            current_active -= 1
            return make_download_result()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(side_effect=slow_download)
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(
            doc_repo=doc_repo, downloader=downloader, processor=processor, concurrency=2
        )
        await service.ingest_pending(concurrency=2)

        assert max_active[0] >= 2


# ── ingest_single ─────────────────────────────────────────────────────────────


class TestIngestSingle:
    async def test_ingest_single_returns_true_on_success(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.find_by_url = AsyncMock(return_value=doc)

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        result = await service.ingest_single(DOC_URL)

        assert result is True

    async def test_ingest_single_returns_false_when_document_not_found(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.find_by_url = AsyncMock(return_value=None)

        service = make_service(doc_repo=doc_repo)
        result = await service.ingest_single(DOC_URL)

        assert result is False

    async def test_ingest_single_returns_false_on_download_error(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.find_by_url = AsyncMock(return_value=doc)

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(side_effect=DownloadError("failed"))

        service = make_service(doc_repo=doc_repo, downloader=downloader)
        result = await service.ingest_single(DOC_URL)

        assert result is False

    async def test_ingest_single_calls_find_by_url_with_correct_url(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.find_by_url = AsyncMock(return_value=doc)

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        await service.ingest_single(DOC_URL)

        doc_repo.find_by_url.assert_called_once_with(DOC_URL)


# ── reprocess_errors ──────────────────────────────────────────────────────────


class TestReprocessErrors:
    async def test_reprocess_errors_resets_error_documents_to_pending(self) -> None:
        error_docs = [make_document(url=DOC_URL), make_document(url=DOC_URL_2, doc_type="csv")]
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_errors = AsyncMock(return_value=error_docs)
        doc_repo.list_pending = AsyncMock(return_value=error_docs)

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        await service.reprocess_errors()

        reset_calls = [
            c for c in doc_repo.update_status.call_args_list if c.args[1] == "pending"
        ]
        assert len(reset_calls) == 2

    async def test_reprocess_errors_returns_ingestion_stats(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_errors = AsyncMock(return_value=[])
        doc_repo.list_pending = AsyncMock(return_value=[])

        service = make_service(doc_repo=doc_repo)
        result = await service.reprocess_errors()

        assert isinstance(result, IngestionStats)

    async def test_reprocess_errors_calls_ingest_pending_after_reset(self) -> None:
        error_doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_errors = AsyncMock(return_value=[error_doc])
        doc_repo.list_pending = AsyncMock(return_value=[error_doc])

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        stats = await service.reprocess_errors()

        # list_pending foi chamado → ingest_pending foi executado (batching: ≥1 call)
        assert doc_repo.list_pending.call_count >= 1
        assert stats.total == 1


# ── get_status ────────────────────────────────────────────────────────────────


class TestGetStatus:
    async def test_get_status_returns_ingestion_status_type(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 5, "processing": 1, "done": 10, "error": 2}
        )
        doc_repo.count_chunks = AsyncMock(return_value=150)
        doc_repo.count_tables = AsyncMock(return_value=20)

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        inconsistency_repo.count_by_status = AsyncMock(
            return_value={"open": 3, "resolved": 1}
        )

        service = make_service(doc_repo=doc_repo, inconsistency_repo=inconsistency_repo)
        status = await service.get_status()

        assert isinstance(status, IngestionStatus)

    async def test_get_status_returns_correct_counts(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 5, "processing": 1, "done": 10, "error": 2}
        )
        doc_repo.count_chunks = AsyncMock(return_value=150)
        doc_repo.count_tables = AsyncMock(return_value=20)

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        inconsistency_repo.count_by_status = AsyncMock(
            return_value={"open": 3, "resolved": 1}
        )

        service = make_service(doc_repo=doc_repo, inconsistency_repo=inconsistency_repo)
        status = await service.get_status()

        assert status.pending == 5
        assert status.processing == 1
        assert status.done == 10
        assert status.error == 2
        assert status.total_chunks == 150
        assert status.total_tables == 20
        assert status.open_inconsistencies == 3

    async def test_get_status_handles_missing_statuses(self) -> None:
        """Quando não há documentos em algum status, conta zero."""
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.count_by_status = AsyncMock(return_value={"done": 10})
        doc_repo.count_chunks = AsyncMock(return_value=100)
        doc_repo.count_tables = AsyncMock(return_value=5)

        inconsistency_repo = AsyncMock(spec=InconsistencyRepository)
        inconsistency_repo.count_by_status = AsyncMock(return_value={})

        service = make_service(doc_repo=doc_repo, inconsistency_repo=inconsistency_repo)
        status = await service.get_status()

        assert status.pending == 0
        assert status.processing == 0
        assert status.error == 0
        assert status.open_inconsistencies == 0
