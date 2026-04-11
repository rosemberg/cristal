"""Domain service: DocumentIngestionService.

Orquestra o pipeline de ingestão de documentos:
  download → processamento → persistência atômica

V2: usa DocumentProcessGateway (port) e registra inconsistências
    na tabela data_inconsistencies via InconsistencyRepository.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.domain.entities.document import Document
from app.domain.ports.inbound.document_ingestion_use_case import DocumentIngestionUseCase
from app.domain.ports.outbound.document_download_gateway import (
    DocumentDownloadGateway,
    DownloadError,
)
from app.domain.ports.outbound.document_process_gateway import (
    DocumentProcessGateway,
    DocumentProcessingError,
)
from app.domain.ports.outbound.document_repository import DocumentRepository
from app.domain.ports.outbound.inconsistency_repository import InconsistencyRepository
from app.domain.value_objects.data_inconsistency import DataInconsistency
from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Classificação de erros
# ─────────────────────────────────────────────────────────────────────────────

_HTTP_STATUS_MAP: dict[int, tuple[str, str]] = {
    404: ("document_not_found", "critical"),
}


def _classify_http_status(status_code: int) -> tuple[str, str]:
    """Retorna (inconsistency_type, severity) baseado no HTTP status code."""
    if status_code == 404:
        return "document_not_found", "critical"
    if status_code >= 500:
        return "document_not_found", "warning"
    return "document_not_found", "warning"


def _classify_download_error(exc: DownloadError) -> tuple[str, str]:
    """Retorna (inconsistency_type, severity) para exceções de download."""
    if exc.is_size_limit:
        return "oversized", "info"
    return "document_not_found", "warning"


def _classify_processing_error(exc: DocumentProcessingError) -> tuple[str, str]:
    """Retorna (inconsistency_type, severity) para erros de processamento."""
    msg = str(exc).lower()
    if "encoding" in msg:
        return "encoding_error", "warning"
    if "no text" in msg or "scanned" in msg:
        return "document_corrupted", "warning"
    return "document_corrupted", "critical"


# ─────────────────────────────────────────────────────────────────────────────
# Serviço
# ─────────────────────────────────────────────────────────────────────────────


class DocumentIngestionService(DocumentIngestionUseCase):
    """Orquestra o pipeline completo de ingestão de documentos."""

    def __init__(
        self,
        doc_repo: DocumentRepository,
        downloader: DocumentDownloadGateway,
        processor: DocumentProcessGateway,
        inconsistency_repo: InconsistencyRepository,
        concurrency: int = 3,
    ) -> None:
        self._doc_repo = doc_repo
        self._downloader = downloader
        self._processor = processor
        self._inconsistency_repo = inconsistency_repo
        self._default_concurrency = concurrency

    # ── Orquestração principal ────────────────────────────────────────────────

    async def ingest_pending(self, concurrency: int = 3) -> IngestionStats:
        """Processa todos os documentos com status 'pending'."""
        start = time.monotonic()
        documents = await self._doc_repo.list_pending()

        processed = 0
        errors = 0
        inconsistencies = 0
        semaphore = asyncio.Semaphore(concurrency)

        async def process_with_semaphore(doc: Document) -> None:
            nonlocal processed, errors, inconsistencies
            async with semaphore:
                success, inc_found = await self._process_document(doc)
                if success:
                    processed += 1
                else:
                    errors += 1
                    inconsistencies += inc_found

        await asyncio.gather(*(process_with_semaphore(doc) for doc in documents))

        return IngestionStats(
            total=len(documents),
            processed=processed,
            errors=errors,
            skipped=0,
            duration_seconds=time.monotonic() - start,
            inconsistencies_found=inconsistencies,
        )

    async def ingest_single(self, document_url: str) -> bool:
        """Processa um único documento por URL. Retorna True se bem-sucedido."""
        doc = await self._doc_repo.find_by_url(document_url)
        if doc is None:
            logger.warning("Documento não encontrado no banco: %s", document_url)
            return False
        success, _ = await self._process_document(doc)
        return success

    async def reprocess_errors(self) -> IngestionStats:
        """Reprocessa documentos que estão com status 'error'."""
        error_docs = await self._doc_repo.list_errors()
        for doc in error_docs:
            await self._doc_repo.update_status(doc.document_url, "pending")
        return await self.ingest_pending(self._default_concurrency)

    async def get_status(self) -> IngestionStatus:
        """Retorna snapshot dos contadores de status do pipeline."""
        counts = await self._doc_repo.count_by_status()
        total_chunks = await self._doc_repo.count_chunks()
        total_tables = await self._doc_repo.count_tables()
        inc_counts = await self._inconsistency_repo.count_by_status()

        return IngestionStatus(
            pending=counts.get("pending", 0),
            processing=counts.get("processing", 0),
            done=counts.get("done", 0),
            error=counts.get("error", 0),
            total_chunks=total_chunks,
            total_tables=total_tables,
            open_inconsistencies=inc_counts.get("open", 0),
        )

    # ── Pipeline de um documento ──────────────────────────────────────────────

    async def _process_document(self, doc: Document) -> tuple[bool, int]:
        """Executa o pipeline completo para um documento.

        Returns:
            (success, inconsistencies_found)
        """
        url = doc.document_url
        await self._doc_repo.update_status(url, "processing")

        try:
            return await self._download_and_process(doc)
        except Exception as exc:
            logger.exception("Erro inesperado ao processar %s", url)
            await self._doc_repo.update_status(url, "error", str(exc))
            await self._upsert_inconsistency(
                doc,
                inconsistency_type="document_corrupted",
                severity="critical",
                detail=str(exc),
                http_status=None,
            )
            return False, 1

    async def _download_and_process(self, doc: Document) -> tuple[bool, int]:
        """Núcleo do pipeline: download → check → process → save."""
        url = doc.document_url

        # ── Download ──────────────────────────────────────────────────────────
        try:
            download_result = await self._downloader.download(url)
        except DownloadError as exc:
            inc_type, severity = _classify_download_error(exc)
            await self._doc_repo.update_status(url, "error", str(exc))
            await self._upsert_inconsistency(
                doc, inc_type, severity, detail=str(exc), http_status=None
            )
            return False, 1

        if download_result.status_code >= 400:
            inc_type, severity = _classify_http_status(download_result.status_code)
            detail = f"HTTP {download_result.status_code}"
            await self._doc_repo.update_status(url, "error", detail)
            await self._upsert_inconsistency(
                doc, inc_type, severity, detail=detail, http_status=download_result.status_code
            )
            return False, 1

        # ── Processamento ──────────────────────────────────────────────────────
        try:
            processed = await self._processor.process(url, download_result.content, doc.type)
        except DocumentProcessingError as exc:
            inc_type, severity = _classify_processing_error(exc)
            await self._doc_repo.update_status(url, "error", str(exc))
            await self._upsert_inconsistency(
                doc, inc_type, severity, detail=str(exc), http_status=None
            )
            return False, 1

        # ── Validação de chunks ────────────────────────────────────────────────
        if not processed.chunks:
            detail = "Nenhum chunk gerado após processamento"
            await self._doc_repo.update_status(url, "error", detail)
            await self._upsert_inconsistency(
                doc, "empty_content", "critical", detail=detail, http_status=None
            )
            return False, 1

        # ── Persistência atômica ───────────────────────────────────────────────
        await self._doc_repo.save_content_atomic(url, processed)
        await self._doc_repo.update_status(url, "done")

        logger.info(
            "Documento processado: %s (%d chunks, %d tabelas)",
            url,
            len(processed.chunks),
            len(processed.tables),
        )
        return True, 0

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _upsert_inconsistency(
        self,
        doc: Document,
        inconsistency_type: str,
        severity: str,
        detail: str,
        http_status: int | None,
    ) -> None:
        now = datetime.now(timezone.utc)
        inconsistency = DataInconsistency(
            id=None,
            resource_type="document",
            severity=severity,
            inconsistency_type=inconsistency_type,
            resource_url=doc.document_url,
            resource_title=doc.title,
            parent_page_url=doc.page_url,
            detail=detail,
            http_status=http_status,
            error_message=detail,
            detected_at=now,
            detected_by="ingestion_pipeline",
            status="open",
            resolved_at=None,
            resolved_by=None,
            resolution_note=None,
            retry_count=0,
            last_checked_at=now,
        )
        await self._inconsistency_repo.upsert(doc.document_url, inconsistency_type, inconsistency)
