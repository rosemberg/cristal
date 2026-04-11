"""Domain service: DataHealthCheckService.

Verifica a saúde dos dados do sistema:
- check_pages: HEAD em todas as páginas crawleadas
- check_documents: HEAD em todos os documentos processados (status='done')
- check_links: HEAD em todos os links de page_links
- check_all: executa os três checks sequencialmente e agrega

Rate limiting: delay configurável entre requests para não sobrecarregar o portal.
Auto-resolução: se um recurso previamente inacessível volta a responder 200,
  a inconsistência aberta é resolvida automaticamente.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.domain.ports.inbound.data_health_check_use_case import DataHealthCheckUseCase
from app.domain.ports.outbound.document_download_gateway import DocumentDownloadGateway
from app.domain.ports.outbound.document_repository import DocumentCheckInfo, DocumentRepository
from app.domain.ports.outbound.inconsistency_repository import InconsistencyRepository
from app.domain.ports.outbound.page_repository import (
    LinkCheckInfo,
    PageCheckInfo,
    PageRepository,
)
from app.domain.value_objects.data_inconsistency import DataInconsistency, HealthCheckReport

logger = logging.getLogger(__name__)


class DataHealthCheckService(DataHealthCheckUseCase):
    """Verifica a saúde dos dados (páginas, documentos, links) e registra inconsistências."""

    def __init__(
        self,
        downloader: DocumentDownloadGateway,
        page_repo: PageRepository,
        doc_repo: DocumentRepository,
        inconsistency_repo: InconsistencyRepository,
        request_delay_ms: float = 200.0,
    ) -> None:
        self._downloader = downloader
        self._page_repo = page_repo
        self._doc_repo = doc_repo
        self._inconsistency_repo = inconsistency_repo
        self._request_delay_ms = request_delay_ms

    # ── check_pages ───────────────────────────────────────────────────────────

    async def check_pages(self, concurrency: int = 5) -> HealthCheckReport:
        """Verifica acessibilidade de todas as páginas cadastradas."""
        start = time.monotonic()
        pages = await self._page_repo.list_all_urls()

        healthy = 0
        issues = 0
        new_inc = 0
        auto_resolved = 0
        by_type: dict[str, int] = {}
        semaphore = asyncio.Semaphore(concurrency)

        async def _check(page: PageCheckInfo) -> None:
            nonlocal healthy, issues, new_inc, auto_resolved
            async with semaphore:
                if self._request_delay_ms > 0:
                    await asyncio.sleep(self._request_delay_ms / 1000)
                result = await self._downloader.check_accessible(page.url)
                if result.accessible:
                    healthy += 1
                    resolved = await self._inconsistency_repo.mark_resolved_by_url(
                        page.url,
                        "page_not_accessible",
                        f"Auto-resolved: recurso acessível em {datetime.now(timezone.utc).isoformat()}",
                    )
                    auto_resolved += resolved
                else:
                    issues += 1
                    severity = "critical" if (result.status_code or 0) >= 500 else "warning"
                    detail = (
                        f"HTTP {result.status_code}"
                        if result.status_code
                        else (result.error or "inacessível")
                    )
                    now = datetime.now(timezone.utc)
                    inc = DataInconsistency(
                        id=None,
                        resource_type="page",
                        severity=severity,
                        inconsistency_type="page_not_accessible",
                        resource_url=page.url,
                        resource_title=page.title,
                        parent_page_url=None,
                        detail=detail,
                        http_status=result.status_code or None,
                        error_message=result.error,
                        detected_at=now,
                        detected_by="health_check",
                        status="open",
                        resolved_at=None,
                        resolved_by=None,
                        resolution_note=None,
                        retry_count=0,
                        last_checked_at=now,
                    )
                    await self._inconsistency_repo.upsert(page.url, "page_not_accessible", inc)
                    new_inc += 1
                    by_type["page_not_accessible"] = by_type.get("page_not_accessible", 0) + 1

        await asyncio.gather(*(_check(p) for p in pages))

        return HealthCheckReport(
            total_checked=len(pages),
            healthy=healthy,
            issues_found=issues,
            new_inconsistencies=new_inc,
            updated_inconsistencies=0,
            auto_resolved=auto_resolved,
            duration_seconds=time.monotonic() - start,
            by_type=by_type,
        )

    # ── check_documents ───────────────────────────────────────────────────────

    async def check_documents(self, concurrency: int = 5) -> HealthCheckReport:
        """Verifica acessibilidade de todos os documentos com status 'done'."""
        start = time.monotonic()
        docs = await self._doc_repo.list_done()

        healthy = 0
        issues = 0
        new_inc = 0
        auto_resolved = 0
        by_type: dict[str, int] = {}
        semaphore = asyncio.Semaphore(concurrency)

        async def _check(doc: DocumentCheckInfo) -> None:
            nonlocal healthy, issues, new_inc, auto_resolved
            async with semaphore:
                if self._request_delay_ms > 0:
                    await asyncio.sleep(self._request_delay_ms / 1000)
                result = await self._downloader.check_accessible(doc.url)

                if not result.accessible:
                    issues += 1
                    severity = "warning" if (result.status_code or 0) >= 500 else "critical"
                    detail = (
                        f"HTTP {result.status_code}"
                        if result.status_code
                        else (result.error or "inacessível")
                    )
                    now = datetime.now(timezone.utc)
                    inc = DataInconsistency(
                        id=None,
                        resource_type="document",
                        severity=severity,
                        inconsistency_type="document_not_found",
                        resource_url=doc.url,
                        resource_title=doc.title,
                        parent_page_url=doc.page_url,
                        detail=detail,
                        http_status=result.status_code or None,
                        error_message=result.error,
                        detected_at=now,
                        detected_by="health_check",
                        status="open",
                        resolved_at=None,
                        resolved_by=None,
                        resolution_note=None,
                        retry_count=0,
                        last_checked_at=now,
                    )
                    await self._inconsistency_repo.upsert(doc.url, "document_not_found", inc)
                    new_inc += 1
                    by_type["document_not_found"] = by_type.get("document_not_found", 0) + 1
                    return

                # Acessível: tenta auto-resolver document_not_found anterior
                resolved = await self._inconsistency_repo.mark_resolved_by_url(
                    doc.url,
                    "document_not_found",
                    f"Auto-resolved: documento acessível em {datetime.now(timezone.utc).isoformat()}",
                )
                auto_resolved += resolved

                # Verifica se Content-Length mudou
                if (
                    result.content_length is not None
                    and doc.stored_content_length is not None
                    and result.content_length != doc.stored_content_length
                ):
                    issues += 1
                    now = datetime.now(timezone.utc)
                    detail = (
                        f"documento pode ter sido atualizado "
                        f"(esperado {doc.stored_content_length}B, atual {result.content_length}B)"
                    )
                    inc = DataInconsistency(
                        id=None,
                        resource_type="document",
                        severity="warning",
                        inconsistency_type="document_corrupted",
                        resource_url=doc.url,
                        resource_title=doc.title,
                        parent_page_url=doc.page_url,
                        detail=detail,
                        http_status=result.status_code,
                        error_message=None,
                        detected_at=now,
                        detected_by="health_check",
                        status="open",
                        resolved_at=None,
                        resolved_by=None,
                        resolution_note=None,
                        retry_count=0,
                        last_checked_at=now,
                    )
                    await self._inconsistency_repo.upsert(doc.url, "document_corrupted", inc)
                    new_inc += 1
                    by_type["document_corrupted"] = by_type.get("document_corrupted", 0) + 1
                else:
                    healthy += 1

        await asyncio.gather(*(_check(d) for d in docs))

        return HealthCheckReport(
            total_checked=len(docs),
            healthy=healthy,
            issues_found=issues,
            new_inconsistencies=new_inc,
            updated_inconsistencies=0,
            auto_resolved=auto_resolved,
            duration_seconds=time.monotonic() - start,
            by_type=by_type,
        )

    # ── check_links ───────────────────────────────────────────────────────────

    async def check_links(self, concurrency: int = 10) -> HealthCheckReport:
        """Verifica validade dos links em page_links."""
        start = time.monotonic()
        links = await self._page_repo.list_all_links()

        healthy = 0
        issues = 0
        new_inc = 0
        auto_resolved = 0
        by_type: dict[str, int] = {}
        semaphore = asyncio.Semaphore(concurrency)

        async def _check(link: LinkCheckInfo) -> None:
            nonlocal healthy, issues, new_inc, auto_resolved
            async with semaphore:
                if self._request_delay_ms > 0:
                    await asyncio.sleep(self._request_delay_ms / 1000)
                result = await self._downloader.check_accessible(link.url)

                if result.accessible:
                    healthy += 1
                    resolved = await self._inconsistency_repo.mark_resolved_by_url(
                        link.url,
                        "broken_link",
                        f"Auto-resolved: link acessível em {datetime.now(timezone.utc).isoformat()}",
                    )
                    auto_resolved += resolved
                else:
                    issues += 1
                    detail = (
                        f"HTTP {result.status_code}"
                        if result.status_code
                        else (result.error or "inacessível")
                    )
                    now = datetime.now(timezone.utc)
                    inc = DataInconsistency(
                        id=None,
                        resource_type="link",
                        severity="warning",
                        inconsistency_type="broken_link",
                        resource_url=link.url,
                        resource_title=link.title,
                        parent_page_url=link.parent_page_url,
                        detail=detail,
                        http_status=result.status_code or None,
                        error_message=result.error,
                        detected_at=now,
                        detected_by="health_check",
                        status="open",
                        resolved_at=None,
                        resolved_by=None,
                        resolution_note=None,
                        retry_count=0,
                        last_checked_at=now,
                    )
                    await self._inconsistency_repo.upsert(link.url, "broken_link", inc)
                    new_inc += 1
                    by_type["broken_link"] = by_type.get("broken_link", 0) + 1

        await asyncio.gather(*(_check(lnk) for lnk in links))

        return HealthCheckReport(
            total_checked=len(links),
            healthy=healthy,
            issues_found=issues,
            new_inconsistencies=new_inc,
            updated_inconsistencies=0,
            auto_resolved=auto_resolved,
            duration_seconds=time.monotonic() - start,
            by_type=by_type,
        )

    # ── check_all ─────────────────────────────────────────────────────────────

    async def check_all(self, concurrency: int = 5) -> HealthCheckReport:
        """Executa check_pages + check_documents + check_links e agrega os resultados."""
        start = time.monotonic()

        page_report = await self.check_pages(concurrency)
        doc_report = await self.check_documents(concurrency)
        link_report = await self.check_links(concurrency * 2)

        by_type: dict[str, int] = {}
        for report in (page_report, doc_report, link_report):
            for k, v in report.by_type.items():
                by_type[k] = by_type.get(k, 0) + v

        return HealthCheckReport(
            total_checked=page_report.total_checked + doc_report.total_checked + link_report.total_checked,
            healthy=page_report.healthy + doc_report.healthy + link_report.healthy,
            issues_found=page_report.issues_found + doc_report.issues_found + link_report.issues_found,
            new_inconsistencies=page_report.new_inconsistencies + doc_report.new_inconsistencies + link_report.new_inconsistencies,
            updated_inconsistencies=0,
            auto_resolved=page_report.auto_resolved + doc_report.auto_resolved + link_report.auto_resolved,
            duration_seconds=time.monotonic() - start,
            by_type=by_type,
        )

    # ── Gerenciamento de inconsistências ──────────────────────────────────────

    async def get_inconsistencies(
        self,
        status: str = "open",
        resource_type: str | None = None,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DataInconsistency]:
        return await self._inconsistency_repo.list_by_status(
            status=status,
            resource_type=resource_type,
            severity=severity,
            limit=limit,
            offset=offset,
        )

    async def resolve_inconsistency(
        self, inconsistency_id: int, resolution_note: str, resolved_by: str
    ) -> None:
        await self._inconsistency_repo.update_status(
            inconsistency_id,
            "resolved",
            resolved_by=resolved_by,
            resolution_note=resolution_note,
        )

    async def acknowledge_inconsistency(self, inconsistency_id: int) -> None:
        await self._inconsistency_repo.update_status(inconsistency_id, "acknowledged")

    async def ignore_inconsistency(self, inconsistency_id: int, reason: str) -> None:
        await self._inconsistency_repo.update_status(
            inconsistency_id,
            "ignored",
            resolution_note=reason,
        )
