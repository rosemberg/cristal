"""Domain service: DocumentIngestionService.

Orquestra o pipeline de ingestão de documentos:
  download → processamento → persistência atômica → embeddings

V2: usa DocumentProcessGateway (port) e registra inconsistências
    na tabela data_inconsistencies via InconsistencyRepository.
V2 RAG: após save_content_atomic, gera embeddings dos chunks e tabelas
    via EmbeddingGateway e persiste na tabela embeddings.
    SHA-256 do texto evita re-gerar embeddings não modificados.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone

from app.domain.entities.document import Document
from app.domain.ports.inbound.document_ingestion_use_case import (
    DocumentIngestionUseCase,
    ProgressCallback,
)

_DEFAULT_BATCH_SIZE = 50
from app.domain.ports.outbound.document_download_gateway import (
    DocumentDownloadGateway,
    DownloadError,
)
from app.domain.ports.outbound.document_process_gateway import (
    DocumentProcessGateway,
    DocumentProcessingError,
)
from app.domain.ports.outbound.document_repository import DocumentRepository
from app.domain.ports.outbound.embedding_gateway import EmbeddingGateway, EmbeddingUnavailableError
from app.domain.ports.outbound.embedding_repository import EmbeddingRecord, EmbeddingRepository
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
        embedding_gateway: EmbeddingGateway | None = None,
        embedding_repo: EmbeddingRepository | None = None,
    ) -> None:
        self._doc_repo = doc_repo
        self._downloader = downloader
        self._processor = processor
        self._inconsistency_repo = inconsistency_repo
        self._default_concurrency = concurrency
        self._embedding_gateway = embedding_gateway
        self._embedding_repo = embedding_repo

    # ── Orquestração principal ────────────────────────────────────────────────

    async def ingest_pending(
        self,
        concurrency: int = 3,
        on_progress: ProgressCallback = None,
        batch_size: int = _DEFAULT_BATCH_SIZE,
    ) -> IngestionStats:
        """Processa todos os documentos com status 'pending' em lotes."""
        start = time.monotonic()
        processed = errors = inconsistencies = counter = 0
        semaphore = asyncio.Semaphore(concurrency)

        # total é obtido no início para informar o callback; pode crescer se
        # reset_stuck_processing adicionar mais pendentes entre lotes.
        counts = await self._doc_repo.count_by_status()
        total = counts.get("pending", 0)

        async def process_one(doc: Document) -> None:
            nonlocal processed, errors, inconsistencies, counter
            async with semaphore:
                success, inc_found = await self._process_document(doc)
                if success:
                    processed += 1
                else:
                    errors += 1
                    inconsistencies += inc_found
                counter += 1
                if on_progress is not None:
                    on_progress(counter, total, doc.document_url, not success)

        batch = await self._doc_repo.list_pending(limit=batch_size)
        while batch:
            await asyncio.gather(*(process_one(doc) for doc in batch))
            batch = await self._doc_repo.list_pending(limit=batch_size)

        return IngestionStats(
            total=counter,
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

    async def reprocess_errors(self, on_progress: ProgressCallback = None) -> IngestionStats:
        """Reprocessa documentos que estão com status 'error'."""
        error_docs = await self._doc_repo.list_errors()
        for doc in error_docs:
            await self._doc_repo.update_status(doc.document_url, "pending")
        return await self.ingest_pending(self._default_concurrency, on_progress=on_progress)

    async def resume(
        self,
        concurrency: int = 3,
        on_progress: ProgressCallback = None,
    ) -> IngestionStats:
        """Retoma pipeline após crash: reseta docs stuck e processa pendentes."""
        reset_count = await self._doc_repo.reset_stuck_processing(stuck_minutes=0)
        if reset_count > 0:
            logger.info(
                "resume: %d documento(s) resetados de 'processing' → 'pending'",
                reset_count,
            )
        return await self.ingest_pending(concurrency=concurrency, on_progress=on_progress)

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

        # ── Geração de embeddings (opcional) ──────────────────────────────────
        if self._embedding_gateway is not None and self._embedding_repo is not None:
            await self._generate_embeddings(doc, processed)

        return True, 0

    # ── Geração de embeddings ─────────────────────────────────────────────────

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    async def _generate_embeddings(
        self, doc: Document, processed: "ProcessedDocument"  # noqa: F821
    ) -> None:
        """Gera embeddings de chunks e tabelas e persiste na tabela embeddings.

        - Pula chunks/tabelas cujo SHA-256 não mudou (re-ingestão sem modificação).
        - Em caso de falha do EmbeddingGateway, registra 'embedding_failed' e continua.
        """
        from app.domain.ports.outbound.document_repository import ProcessedDocument  # noqa: PLC0415

        url = doc.document_url

        # Busca os chunks salvos com IDs do banco (save_content_atomic não retorna IDs)
        saved_chunks = await self._doc_repo.get_chunks(url)
        saved_tables = await self._doc_repo.get_tables(url)

        # Hashes existentes para skip rápido em re-ingestão
        existing_chunk_hashes = await self._embedding_repo.get_existing_hashes("chunk")
        existing_table_hashes = await self._embedding_repo.get_existing_hashes("table")

        # ── Chunks ────────────────────────────────────────────────────────────
        chunks_to_embed: list[tuple[int, str]] = []  # (id, text)
        for chunk in saved_chunks:
            text = chunk.text or ""
            if not text.strip():
                continue
            h = self._sha256(text)
            if existing_chunk_hashes.get(chunk.id) == h:
                continue  # não mudou — pular
            chunks_to_embed.append((chunk.id, text))

        if chunks_to_embed:
            texts = [t for _, t in chunks_to_embed]
            try:
                embeddings = await self._embedding_gateway.embed_batch(
                    texts, task_type="RETRIEVAL_DOCUMENT"
                )
                records = [
                    EmbeddingRecord(
                        source_type="chunk",
                        source_id=cid,
                        embedding=emb,
                        source_text_hash=self._sha256(text),
                    )
                    for (cid, text), emb in zip(chunks_to_embed, embeddings)
                ]
                await self._embedding_repo.save_batch(records)
                logger.info("Embeddings gerados: %d chunks (%s)", len(records), url)
            except EmbeddingUnavailableError as exc:
                logger.warning("EmbeddingGateway indisponível para %s: %s", url, exc)
                await self._register_embedding_failure(doc, str(exc))
            except Exception as exc:  # noqa: BLE001
                logger.error("Falha ao gerar embeddings de chunks para %s: %s", url, exc)
                await self._register_embedding_failure(doc, str(exc))

        # ── Tabelas ───────────────────────────────────────────────────────────
        tables_to_embed: list[tuple[int, str]] = []
        for table in saved_tables:
            # Texto representativo: caption + headers concatenados
            parts = [table.caption or ""]
            if table.headers:
                parts.append(" | ".join(str(h) for h in table.headers))
            if table.rows:
                # Inclui até 5 linhas para contexto semântico
                for row in table.rows[:5]:
                    parts.append(" | ".join(str(c) for c in row))
            text = " ".join(p for p in parts if p).strip()
            if not text:
                continue
            h = self._sha256(text)
            if existing_table_hashes.get(table.id) == h:
                continue
            tables_to_embed.append((table.id, text))

        if tables_to_embed:
            texts = [t for _, t in tables_to_embed]
            try:
                embeddings = await self._embedding_gateway.embed_batch(
                    texts, task_type="RETRIEVAL_DOCUMENT"
                )
                records = [
                    EmbeddingRecord(
                        source_type="table",
                        source_id=tid,
                        embedding=emb,
                        source_text_hash=self._sha256(text),
                    )
                    for (tid, text), emb in zip(tables_to_embed, embeddings)
                ]
                await self._embedding_repo.save_batch(records)
                logger.info("Embeddings gerados: %d tabelas (%s)", len(records), url)
            except EmbeddingUnavailableError as exc:
                logger.warning("EmbeddingGateway indisponível para tabelas %s: %s", url, exc)
            except Exception as exc:  # noqa: BLE001
                logger.error("Falha ao gerar embeddings de tabelas para %s: %s", url, exc)

    async def _register_embedding_failure(self, doc: Document, detail: str) -> None:
        """Registra inconsistência do tipo 'embedding_failed' — não bloqueia ingestão."""
        now = datetime.now(timezone.utc)
        inconsistency = DataInconsistency(
            id=None,
            resource_type="document",
            severity="warning",
            inconsistency_type="embedding_failed",
            resource_url=doc.document_url,
            resource_title=doc.title,
            parent_page_url=doc.page_url,
            detail=detail,
            http_status=None,
            error_message=detail,
            detected_at=now,
            detected_by="embedding_pipeline",
            status="open",
            resolved_at=None,
            resolved_by=None,
            resolution_note=None,
            retry_count=0,
            last_checked_at=now,
        )
        try:
            await self._inconsistency_repo.upsert(
                doc.document_url, "embedding_failed", inconsistency
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Falha ao registrar embedding_failed: %s", exc)

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
