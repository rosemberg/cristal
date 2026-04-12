"""Unit tests — Robustez e retomada do pipeline de ingestão.

Cobre:
- DocumentIngestionService.resume(): reseta stuck e processa pendentes
- DocumentRepository.reset_stuck_processing(): contrato do port
- Batching em ingest_pending(): list_pending chamado em lotes
- CLI DocumentIngesterCLI.resume(): argparse e output
"""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, call, patch

import pytest

from app.domain.entities.document import Document
from app.domain.ports.inbound.document_ingestion_use_case import DocumentIngestionUseCase
from app.domain.ports.outbound.document_download_gateway import (
    DocumentDownloadGateway,
    DownloadResult,
)
from app.domain.ports.outbound.document_process_gateway import DocumentProcessGateway
from app.domain.ports.outbound.document_repository import DocumentRepository, ProcessedDocument
from app.domain.ports.outbound.inconsistency_repository import InconsistencyRepository
from app.domain.services.document_ingestion_service import DocumentIngestionService
from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus

# ── Constantes ────────────────────────────────────────────────────────────────

DOC_URL = "https://www.tre-pi.jus.br/doc/relatorio.pdf"
DOC_URL_2 = "https://www.tre-pi.jus.br/doc/planilha.csv"
PAGE_URL = "https://www.tre-pi.jus.br/transparencia"
PDF_BYTES = b"%PDF-1.4 fake"


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_document(url: str = DOC_URL) -> Document:
    return Document(
        id=1,
        page_url=PAGE_URL,
        document_url=url,
        type="pdf",
        is_processed=False,
        title="Relatório",
    )


def make_download_result() -> DownloadResult:
    return DownloadResult(
        content=PDF_BYTES,
        status_code=200,
        content_type="application/pdf",
        size_bytes=len(PDF_BYTES),
    )


def make_processed_document() -> ProcessedDocument:
    from app.domain.entities.chunk import DocumentChunk
    chunk = DocumentChunk(
        id=None,  # type: ignore[arg-type]
        document_url=DOC_URL,
        chunk_index=0,
        text="texto extraído do documento",
        token_count=5,
    )
    return ProcessedDocument(document_url=DOC_URL, text="texto", chunks=[chunk])


def make_service(
    doc_repo: DocumentRepository | None = None,
    downloader: DocumentDownloadGateway | None = None,
    processor: DocumentProcessGateway | None = None,
    inconsistency_repo: InconsistencyRepository | None = None,
) -> DocumentIngestionService:
    repo = doc_repo or AsyncMock(spec=DocumentRepository)
    if doc_repo is None:
        repo.list_pending = AsyncMock(return_value=[])
        repo.reset_stuck_processing = AsyncMock(return_value=0)
        repo.count_by_status = AsyncMock(return_value={"pending": 0, "processing": 0, "done": 0, "error": 0})
    dl = downloader or AsyncMock(spec=DocumentDownloadGateway)
    pr = processor or AsyncMock(spec=DocumentProcessGateway)
    inc = inconsistency_repo or AsyncMock(spec=InconsistencyRepository)
    return DocumentIngestionService(
        doc_repo=repo,
        downloader=dl,
        processor=pr,
        inconsistency_repo=inc,
    )


def make_ingestion_status(pending: int = 5, processing: int = 2) -> IngestionStatus:
    return IngestionStatus(
        pending=pending,
        processing=processing,
        done=10,
        error=1,
        total_chunks=100,
        total_tables=5,
        open_inconsistencies=0,
    )


# ── TestResumePipeline — DocumentIngestionService.resume() ────────────────────


@pytest.mark.asyncio
class TestResumePipeline:
    async def test_resume_chama_reset_stuck_processing_com_zero_minutos(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.reset_stuck_processing = AsyncMock(return_value=0)
        doc_repo.list_pending = AsyncMock(return_value=[])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 0, "processing": 0, "done": 0, "error": 0}
        )
        service = make_service(doc_repo=doc_repo)

        await service.resume()

        doc_repo.reset_stuck_processing.assert_awaited_once_with(stuck_minutes=0)

    async def test_resume_retorna_ingestion_stats(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.reset_stuck_processing = AsyncMock(return_value=0)
        doc_repo.list_pending = AsyncMock(return_value=[])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 0, "processing": 0, "done": 0, "error": 0}
        )
        service = make_service(doc_repo=doc_repo)

        result = await service.resume()

        assert isinstance(result, IngestionStats)

    async def test_resume_processa_documentos_apos_reset(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.reset_stuck_processing = AsyncMock(return_value=1)
        # primeiro call retorna doc (recém-liberado), segundo retorna vazio
        doc_repo.list_pending = AsyncMock(side_effect=[[doc], []])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 1, "processing": 0, "done": 0, "error": 0}
        )
        doc_repo.update_status = AsyncMock()
        doc_repo.save_content_atomic = AsyncMock()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        stats = await service.resume()

        assert stats.processed == 1
        assert stats.errors == 0

    async def test_resume_sem_docs_stuck_processa_apenas_pendentes(self) -> None:
        doc = make_document(DOC_URL_2)
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.reset_stuck_processing = AsyncMock(return_value=0)
        doc_repo.list_pending = AsyncMock(side_effect=[[doc], []])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 1, "processing": 0, "done": 0, "error": 0}
        )
        doc_repo.update_status = AsyncMock()
        doc_repo.save_content_atomic = AsyncMock()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        stats = await service.resume()

        assert stats.processed == 1

    async def test_resume_repassa_concurrency(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.reset_stuck_processing = AsyncMock(return_value=0)
        doc_repo.list_pending = AsyncMock(return_value=[])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 0, "processing": 0, "done": 0, "error": 0}
        )

        service = make_service(doc_repo=doc_repo)
        # concurrency=7 não deve lançar exceção e deve ser respeitado pelo Semaphore
        result = await service.resume(concurrency=7)

        assert isinstance(result, IngestionStats)

    async def test_resume_repassa_on_progress_callback(self) -> None:
        doc = make_document()
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.reset_stuck_processing = AsyncMock(return_value=0)
        doc_repo.list_pending = AsyncMock(side_effect=[[doc], []])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 1, "processing": 0, "done": 0, "error": 0}
        )
        doc_repo.update_status = AsyncMock()
        doc_repo.save_content_atomic = AsyncMock()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        calls: list[tuple[int, int, str, bool]] = []

        def on_progress(current: int, total: int, url: str, is_error: bool) -> None:
            calls.append((current, total, url, is_error))

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        await service.resume(on_progress=on_progress)

        assert len(calls) == 1
        assert calls[0][2] == doc.document_url
        assert calls[0][3] is False  # sucesso

    async def test_resume_loga_documentos_resetados(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.reset_stuck_processing = AsyncMock(return_value=3)
        doc_repo.list_pending = AsyncMock(return_value=[])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 0, "processing": 0, "done": 0, "error": 0}
        )
        service = make_service(doc_repo=doc_repo)

        with patch(
            "app.domain.services.document_ingestion_service.logger"
        ) as mock_logger:
            await service.resume()
            mock_logger.info.assert_called_once()
            logged_msg = mock_logger.info.call_args[0][0]
            assert "3" in mock_logger.info.call_args[0][1:].__str__() or "3" in str(
                mock_logger.info.call_args
            )

    async def test_resume_nao_loga_quando_zero_resetados(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.reset_stuck_processing = AsyncMock(return_value=0)
        doc_repo.list_pending = AsyncMock(return_value=[])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 0, "processing": 0, "done": 0, "error": 0}
        )
        service = make_service(doc_repo=doc_repo)

        with patch(
            "app.domain.services.document_ingestion_service.logger"
        ) as mock_logger:
            await service.resume()
            mock_logger.info.assert_not_called()


# ── TestBatchingIngestPending ─────────────────────────────────────────────────


@pytest.mark.asyncio
class TestBatchingIngestPending:
    async def test_ingest_pending_processa_em_lotes(self) -> None:
        """list_pending deve ser chamado em loop até retornar lista vazia."""
        doc1 = make_document(DOC_URL)
        doc2 = make_document(DOC_URL_2)
        doc_repo = AsyncMock(spec=DocumentRepository)
        # lote 1: 2 docs; lote 2: vazio (fim)
        doc_repo.list_pending = AsyncMock(side_effect=[[doc1, doc2], []])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 2, "processing": 0, "done": 0, "error": 0}
        )
        doc_repo.update_status = AsyncMock()
        doc_repo.save_content_atomic = AsyncMock()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        stats = await service.ingest_pending(batch_size=50)

        assert stats.processed == 2
        assert doc_repo.list_pending.call_count == 2

    async def test_ingest_pending_multiplos_lotes(self) -> None:
        """Deve continuar processando enquanto houver pendentes."""
        doc1 = make_document(DOC_URL)
        doc2 = make_document(DOC_URL_2)
        doc3 = make_document("https://tre-pi.jus.br/doc3.pdf")
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(side_effect=[[doc1], [doc2], [doc3], []])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 3, "processing": 0, "done": 0, "error": 0}
        )
        doc_repo.update_status = AsyncMock()
        doc_repo.save_content_atomic = AsyncMock()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        stats = await service.ingest_pending(batch_size=1)

        assert stats.processed == 3
        assert doc_repo.list_pending.call_count == 4

    async def test_ingest_pending_repassa_batch_size_ao_repo(self) -> None:
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(return_value=[])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 0, "processing": 0, "done": 0, "error": 0}
        )
        service = make_service(doc_repo=doc_repo)

        await service.ingest_pending(batch_size=25)

        doc_repo.list_pending.assert_awaited_with(limit=25)

    async def test_ingest_pending_stats_total_reflete_processados(self) -> None:
        doc1 = make_document(DOC_URL)
        doc2 = make_document(DOC_URL_2)
        doc_repo = AsyncMock(spec=DocumentRepository)
        doc_repo.list_pending = AsyncMock(side_effect=[[doc1, doc2], []])
        doc_repo.count_by_status = AsyncMock(
            return_value={"pending": 2, "processing": 0, "done": 0, "error": 0}
        )
        doc_repo.update_status = AsyncMock()
        doc_repo.save_content_atomic = AsyncMock()

        downloader = AsyncMock(spec=DocumentDownloadGateway)
        downloader.download = AsyncMock(return_value=make_download_result())
        processor = AsyncMock(spec=DocumentProcessGateway)
        processor.process = AsyncMock(return_value=make_processed_document())

        service = make_service(doc_repo=doc_repo, downloader=downloader, processor=processor)
        stats = await service.ingest_pending()

        assert stats.total == 2


# ── TestDocumentIngesterCLIResume ─────────────────────────────────────────────


@pytest.mark.asyncio
class TestDocumentIngesterCLIResume:
    def _make_ing(self) -> AsyncMock:
        ing = AsyncMock()
        ing.get_status = AsyncMock(return_value=make_ingestion_status())
        ing.resume = AsyncMock(
            return_value=IngestionStats(
                total=7,
                processed=6,
                errors=1,
                skipped=0,
                duration_seconds=42.0,
                inconsistencies_found=1,
            )
        )
        return ing

    async def test_resume_chama_service_resume_com_concurrency_padrao(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from app.adapters.inbound.cli.document_ingester import DocumentIngesterCLI

        ing = self._make_ing()
        hc = AsyncMock()
        cli = DocumentIngesterCLI(ingestion_service=ing, health_check_service=hc)

        await cli.resume()

        ing.resume.assert_awaited_once_with(concurrency=3, on_progress=ANY)

    async def test_resume_chama_service_resume_com_concurrency_customizado(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from app.adapters.inbound.cli.document_ingester import DocumentIngesterCLI

        ing = self._make_ing()
        hc = AsyncMock()
        cli = DocumentIngesterCLI(ingestion_service=ing, health_check_service=hc)

        await cli.resume(concurrency=8)

        ing.resume.assert_awaited_once_with(concurrency=8, on_progress=ANY)

    async def test_resume_imprime_resumo_no_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from app.adapters.inbound.cli.document_ingester import DocumentIngesterCLI

        ing = self._make_ing()
        hc = AsyncMock()
        cli = DocumentIngesterCLI(ingestion_service=ing, health_check_service=hc)

        await cli.resume()

        err = capsys.readouterr().err
        assert "6" in err   # processados
        assert "1" in err   # erros
        assert "etomad" in err.lower() or "Resumo" in err

    def test_main_resume_arg_roteia_para_cli_resume(self) -> None:
        from unittest.mock import MagicMock, patch as _patch
        from app.adapters.inbound.cli.document_ingester import main

        with (
            _patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            _patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
            _patch("sys.argv", ["ingester", "--resume"]),
        ):
            mock_build.return_value = MagicMock()
            main()
            mock_async.assert_called_once()
