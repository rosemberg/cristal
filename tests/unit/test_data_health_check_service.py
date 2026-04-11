"""Unit tests — DataHealthCheckService (Etapa 5).

TDD RED → GREEN para o serviço de verificação de saúde de dados.

Cobre:
- check_pages: acessível, 404, 5xx, timeout, auto-resolve, rate limit
- check_documents: 404, content-length diferente, auto-resolve
- check_links: 404, timeout, auto-resolve
- check_all: agregação de reports
- Gerenciamento de inconsistências: get, resolve, acknowledge, ignore
- Verificações de contratos: resource_type, severity, detected_by
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock, call, patch

import pytest

from app.domain.ports.inbound.data_health_check_use_case import DataHealthCheckUseCase
from app.domain.ports.outbound.document_download_gateway import (
    AccessCheckResult,
    DocumentDownloadGateway,
)
from app.domain.ports.outbound.document_repository import (
    DocumentCheckInfo,
    DocumentRepository,
)
from app.domain.ports.outbound.inconsistency_repository import InconsistencyRepository
from app.domain.ports.outbound.page_repository import (
    LinkCheckInfo,
    PageCheckInfo,
    PageRepository,
)
from app.domain.services.data_health_check_service import DataHealthCheckService
from app.domain.value_objects.data_inconsistency import DataInconsistency, HealthCheckReport

# ── Constantes ────────────────────────────────────────────────────────────────

PAGE_URL = "https://www.tre-pi.jus.br/transparencia"
PAGE_URL_2 = "https://www.tre-pi.jus.br/licitacoes"
DOC_URL = "https://www.tre-pi.jus.br/doc/resolucao-123.pdf"
DOC_URL_2 = "https://www.tre-pi.jus.br/doc/planilha.csv"
LINK_URL = "https://www.tre-pi.jus.br/link/outro"
NOW = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_access_result(
    url: str = PAGE_URL,
    accessible: bool = True,
    status_code: int = 200,
    content_length: int | None = None,
    error: str | None = None,
) -> AccessCheckResult:
    return AccessCheckResult(
        url=url,
        accessible=accessible,
        status_code=status_code,
        content_type="text/html" if accessible else None,
        content_length=content_length,
        error=error,
        response_time_ms=120.0,
    )


def make_page(url: str = PAGE_URL, title: str = "Página de Teste") -> PageCheckInfo:
    return PageCheckInfo(url=url, title=title)


def make_link(
    url: str = LINK_URL,
    title: str = "Link Teste",
    parent: str = PAGE_URL,
) -> LinkCheckInfo:
    return LinkCheckInfo(url=url, title=title, parent_page_url=parent)


def make_doc(
    url: str = DOC_URL,
    stored_content_length: int | None = None,
) -> DocumentCheckInfo:
    return DocumentCheckInfo(
        url=url,
        title="Resolução 123",
        page_url=PAGE_URL,
        stored_content_length=stored_content_length,
    )


def make_inconsistency(inc_id: int = 1) -> DataInconsistency:
    return DataInconsistency(
        id=inc_id,
        resource_type="page",
        severity="warning",
        inconsistency_type="page_not_accessible",
        resource_url=PAGE_URL,
        resource_title="Transparência",
        parent_page_url=None,
        detail="HTTP 404",
        http_status=404,
        error_message=None,
        detected_at=NOW,
        detected_by="health_check",
        status="open",
        resolved_at=None,
        resolved_by=None,
        resolution_note=None,
        retry_count=0,
        last_checked_at=NOW,
    )


def make_service(
    downloader: DocumentDownloadGateway | None = None,
    page_repo: PageRepository | None = None,
    doc_repo: DocumentRepository | None = None,
    inconsistency_repo: InconsistencyRepository | None = None,
    request_delay_ms: float = 0.0,  # sem delay nos testes
) -> DataHealthCheckService:
    return DataHealthCheckService(
        downloader=downloader or AsyncMock(spec=DocumentDownloadGateway),
        page_repo=page_repo or AsyncMock(spec=PageRepository),
        doc_repo=doc_repo or AsyncMock(spec=DocumentRepository),
        inconsistency_repo=inconsistency_repo or AsyncMock(spec=InconsistencyRepository),
        request_delay_ms=request_delay_ms,
    )


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def downloader() -> AsyncMock:
    return AsyncMock(spec=DocumentDownloadGateway)


@pytest.fixture
def page_repo() -> AsyncMock:
    return AsyncMock(spec=PageRepository)


@pytest.fixture
def doc_repo() -> AsyncMock:
    return AsyncMock(spec=DocumentRepository)


@pytest.fixture
def inconsistency_repo() -> AsyncMock:
    repo = AsyncMock(spec=InconsistencyRepository)
    repo.upsert.return_value = 1
    repo.mark_resolved_by_url.return_value = 0
    return repo


@pytest.fixture
def service(downloader, page_repo, doc_repo, inconsistency_repo) -> DataHealthCheckService:
    return DataHealthCheckService(
        downloader=downloader,
        page_repo=page_repo,
        doc_repo=doc_repo,
        inconsistency_repo=inconsistency_repo,
        request_delay_ms=0.0,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Contrato de interface
# ══════════════════════════════════════════════════════════════════════════════


def test_service_implements_use_case(service):
    assert isinstance(service, DataHealthCheckUseCase)


# ══════════════════════════════════════════════════════════════════════════════
# check_pages
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_check_pages_empty_list(service, page_repo, inconsistency_repo):
    page_repo.list_all_urls.return_value = []

    report = await service.check_pages()

    assert report.total_checked == 0
    assert report.healthy == 0
    assert report.issues_found == 0
    assert report.new_inconsistencies == 0
    assert report.auto_resolved == 0
    inconsistency_repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_check_pages_all_accessible(service, page_repo, downloader, inconsistency_repo):
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result(accessible=True)
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_pages()

    assert report.total_checked == 1
    assert report.healthy == 1
    assert report.issues_found == 0
    assert report.new_inconsistencies == 0
    inconsistency_repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_check_pages_404_creates_page_not_accessible(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=404
    )

    report = await service.check_pages()

    assert report.total_checked == 1
    assert report.healthy == 0
    assert report.issues_found == 1
    assert report.new_inconsistencies == 1

    inconsistency_repo.upsert.assert_called_once()
    _, inc_type, inc = inconsistency_repo.upsert.call_args[0]
    assert inc_type == "page_not_accessible"
    assert inc.resource_type == "page"
    assert inc.resource_url == PAGE_URL
    assert inc.http_status == 404
    assert inc.detected_by == "health_check"
    assert inc.status == "open"


@pytest.mark.asyncio
async def test_check_pages_5xx_creates_critical_inconsistency(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=503
    )

    report = await service.check_pages()

    assert report.issues_found == 1
    _, inc_type, inc = inconsistency_repo.upsert.call_args[0]
    assert inc_type == "page_not_accessible"
    assert inc.severity == "critical"


@pytest.mark.asyncio
async def test_check_pages_404_creates_warning_inconsistency(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=404
    )

    await service.check_pages()

    _, _, inc = inconsistency_repo.upsert.call_args[0]
    assert inc.severity == "warning"


@pytest.mark.asyncio
async def test_check_pages_timeout_creates_page_not_accessible(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=0, error="timeout"
    )

    report = await service.check_pages()

    assert report.issues_found == 1
    _, inc_type, inc = inconsistency_repo.upsert.call_args[0]
    assert inc_type == "page_not_accessible"
    assert inc.error_message == "timeout"


@pytest.mark.asyncio
async def test_check_pages_auto_resolve_when_recovered(
    service, page_repo, downloader, inconsistency_repo
):
    """Página previamente 404 agora retorna 200 → auto-resolve."""
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result(accessible=True)
    inconsistency_repo.mark_resolved_by_url.return_value = 1  # 1 inconsistência resolvida

    report = await service.check_pages()

    assert report.auto_resolved == 1
    assert report.healthy == 1
    inconsistency_repo.mark_resolved_by_url.assert_called_once_with(
        PAGE_URL, "page_not_accessible", ANY
    )


@pytest.mark.asyncio
async def test_check_pages_no_auto_resolve_when_no_prior_inconsistency(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result(accessible=True)
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_pages()

    assert report.auto_resolved == 0


@pytest.mark.asyncio
async def test_check_pages_by_type_tracking(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page(PAGE_URL), make_page(PAGE_URL_2)]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=404
    )

    report = await service.check_pages()

    assert report.by_type.get("page_not_accessible", 0) == 2


@pytest.mark.asyncio
async def test_check_pages_multiple_mixed_results(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page(PAGE_URL), make_page(PAGE_URL_2)]
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    def side_effect(url: str) -> AccessCheckResult:
        if url == PAGE_URL:
            return make_access_result(accessible=True)
        return make_access_result(url=url, accessible=False, status_code=404)

    downloader.check_accessible.side_effect = side_effect

    report = await service.check_pages()

    assert report.total_checked == 2
    assert report.healthy == 1
    assert report.issues_found == 1


@pytest.mark.asyncio
async def test_check_pages_report_duration_is_positive(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result()
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_pages()

    assert report.duration_seconds >= 0


@pytest.mark.asyncio
async def test_check_pages_respects_rate_limit(
    page_repo, downloader, inconsistency_repo
):
    """Delay de 200ms é aplicado a cada request."""
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result()
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    svc = DataHealthCheckService(
        downloader=downloader,
        page_repo=page_repo,
        doc_repo=AsyncMock(spec=DocumentRepository),
        inconsistency_repo=inconsistency_repo,
        request_delay_ms=200.0,
    )

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await svc.check_pages()
        mock_sleep.assert_called_once_with(0.2)


@pytest.mark.asyncio
async def test_check_pages_no_rate_limit_when_zero(
    page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    downloader.check_accessible.return_value = make_access_result()
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    svc = make_service(
        downloader=downloader,
        page_repo=page_repo,
        inconsistency_repo=inconsistency_repo,
        request_delay_ms=0.0,
    )

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await svc.check_pages()
        mock_sleep.assert_not_called()


@pytest.mark.asyncio
async def test_check_pages_all_urls_are_checked(
    service, page_repo, downloader, inconsistency_repo
):
    pages = [make_page(f"https://www.tre-pi.jus.br/p{i}") for i in range(5)]
    page_repo.list_all_urls.return_value = pages
    downloader.check_accessible.return_value = make_access_result()
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_pages(concurrency=3)

    assert report.total_checked == 5
    assert downloader.check_accessible.call_count == 5


# ══════════════════════════════════════════════════════════════════════════════
# check_documents
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_check_documents_empty_list(service, doc_repo, inconsistency_repo):
    doc_repo.list_done.return_value = []

    report = await service.check_documents()

    assert report.total_checked == 0
    assert report.healthy == 0
    inconsistency_repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_check_documents_accessible_same_content_length(
    service, doc_repo, downloader, inconsistency_repo
):
    doc_repo.list_done.return_value = [make_doc(stored_content_length=5000)]
    downloader.check_accessible.return_value = make_access_result(
        url=DOC_URL, accessible=True, status_code=200, content_length=5000
    )
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_documents()

    assert report.healthy == 1
    assert report.issues_found == 0
    inconsistency_repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_check_documents_404_creates_document_not_found_critical(
    service, doc_repo, downloader, inconsistency_repo
):
    doc_repo.list_done.return_value = [make_doc()]
    downloader.check_accessible.return_value = make_access_result(
        url=DOC_URL, accessible=False, status_code=404
    )

    report = await service.check_documents()

    assert report.issues_found == 1
    _, inc_type, inc = inconsistency_repo.upsert.call_args[0]
    assert inc_type == "document_not_found"
    assert inc.severity == "critical"
    assert inc.resource_type == "document"
    assert inc.resource_url == DOC_URL
    assert inc.detected_by == "health_check"


@pytest.mark.asyncio
async def test_check_documents_5xx_creates_document_not_found_warning(
    service, doc_repo, downloader, inconsistency_repo
):
    doc_repo.list_done.return_value = [make_doc()]
    downloader.check_accessible.return_value = make_access_result(
        url=DOC_URL, accessible=False, status_code=500
    )

    await service.check_documents()

    _, inc_type, inc = inconsistency_repo.upsert.call_args[0]
    assert inc_type == "document_not_found"
    assert inc.severity == "warning"


@pytest.mark.asyncio
async def test_check_documents_content_length_changed_creates_corrupted(
    service, doc_repo, downloader, inconsistency_repo
):
    """Content-Length HTTP diferente do armazenado → document_corrupted."""
    doc_repo.list_done.return_value = [make_doc(stored_content_length=5000)]
    downloader.check_accessible.return_value = make_access_result(
        url=DOC_URL, accessible=True, status_code=200, content_length=6500
    )
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_documents()

    assert report.issues_found == 1
    inconsistency_repo.upsert.assert_called_once()
    _, inc_type, inc = inconsistency_repo.upsert.call_args[0]
    assert inc_type == "document_corrupted"
    assert "atualizado" in inc.detail.lower()


@pytest.mark.asyncio
async def test_check_documents_no_stored_length_skips_comparison(
    service, doc_repo, downloader, inconsistency_repo
):
    """Sem stored_content_length → não compara, não cria inconsistência."""
    doc_repo.list_done.return_value = [make_doc(stored_content_length=None)]
    downloader.check_accessible.return_value = make_access_result(
        url=DOC_URL, accessible=True, status_code=200, content_length=6500
    )
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_documents()

    assert report.healthy == 1
    assert report.issues_found == 0
    inconsistency_repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_check_documents_null_content_length_skips_comparison(
    service, doc_repo, downloader, inconsistency_repo
):
    """Content-Length ausente na resposta HTTP → não compara."""
    doc_repo.list_done.return_value = [make_doc(stored_content_length=5000)]
    downloader.check_accessible.return_value = make_access_result(
        url=DOC_URL, accessible=True, status_code=200, content_length=None
    )
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_documents()

    assert report.healthy == 1
    inconsistency_repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_check_documents_auto_resolve_when_recovered(
    service, doc_repo, downloader, inconsistency_repo
):
    """Documento 404 que voltou a ser acessível → auto-resolve document_not_found."""
    doc_repo.list_done.return_value = [make_doc()]
    downloader.check_accessible.return_value = make_access_result(
        url=DOC_URL, accessible=True, status_code=200
    )
    inconsistency_repo.mark_resolved_by_url.return_value = 1

    report = await service.check_documents()

    assert report.auto_resolved == 1
    inconsistency_repo.mark_resolved_by_url.assert_called_once_with(
        DOC_URL, "document_not_found", ANY
    )


@pytest.mark.asyncio
async def test_check_documents_resource_type_is_document(
    service, doc_repo, downloader, inconsistency_repo
):
    doc_repo.list_done.return_value = [make_doc()]
    downloader.check_accessible.return_value = make_access_result(
        url=DOC_URL, accessible=False, status_code=404
    )

    await service.check_documents()

    _, _, inc = inconsistency_repo.upsert.call_args[0]
    assert inc.resource_type == "document"


# ══════════════════════════════════════════════════════════════════════════════
# check_links
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_check_links_empty_list(service, page_repo, inconsistency_repo):
    page_repo.list_all_links.return_value = []

    report = await service.check_links()

    assert report.total_checked == 0
    inconsistency_repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_check_links_accessible_no_inconsistency(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_links.return_value = [make_link()]
    downloader.check_accessible.return_value = make_access_result(url=LINK_URL)
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_links()

    assert report.healthy == 1
    assert report.issues_found == 0
    inconsistency_repo.upsert.assert_not_called()


@pytest.mark.asyncio
async def test_check_links_404_creates_broken_link(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_links.return_value = [make_link()]
    downloader.check_accessible.return_value = make_access_result(
        url=LINK_URL, accessible=False, status_code=404
    )

    report = await service.check_links()

    assert report.issues_found == 1
    _, inc_type, inc = inconsistency_repo.upsert.call_args[0]
    assert inc_type == "broken_link"
    assert inc.resource_type == "link"
    assert inc.resource_url == LINK_URL
    assert inc.parent_page_url == PAGE_URL
    assert inc.detected_by == "health_check"


@pytest.mark.asyncio
async def test_check_links_timeout_creates_broken_link(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_links.return_value = [make_link()]
    downloader.check_accessible.return_value = make_access_result(
        url=LINK_URL, accessible=False, status_code=0, error="timeout"
    )

    await service.check_links()

    _, inc_type, _ = inconsistency_repo.upsert.call_args[0]
    assert inc_type == "broken_link"


@pytest.mark.asyncio
async def test_check_links_auto_resolve_when_recovered(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_links.return_value = [make_link()]
    downloader.check_accessible.return_value = make_access_result(url=LINK_URL)
    inconsistency_repo.mark_resolved_by_url.return_value = 1

    report = await service.check_links()

    assert report.auto_resolved == 1
    inconsistency_repo.mark_resolved_by_url.assert_called_once_with(
        LINK_URL, "broken_link", ANY
    )


@pytest.mark.asyncio
async def test_check_links_parent_page_url_recorded(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_links.return_value = [make_link(parent=PAGE_URL_2)]
    downloader.check_accessible.return_value = make_access_result(
        url=LINK_URL, accessible=False, status_code=404
    )

    await service.check_links()

    _, _, inc = inconsistency_repo.upsert.call_args[0]
    assert inc.parent_page_url == PAGE_URL_2


@pytest.mark.asyncio
async def test_check_links_multiple_broken(
    service, page_repo, downloader, inconsistency_repo
):
    link1 = make_link(url="https://www.tre-pi.jus.br/l1")
    link2 = make_link(url="https://www.tre-pi.jus.br/l2")
    page_repo.list_all_links.return_value = [link1, link2]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=404
    )

    report = await service.check_links()

    assert report.total_checked == 2
    assert report.issues_found == 2
    assert inconsistency_repo.upsert.call_count == 2


# ══════════════════════════════════════════════════════════════════════════════
# check_all
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_check_all_aggregates_total_checked(
    service, page_repo, doc_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    page_repo.list_all_links.return_value = [make_link()]
    doc_repo.list_done.return_value = [make_doc()]
    downloader.check_accessible.return_value = make_access_result()
    inconsistency_repo.mark_resolved_by_url.return_value = 0

    report = await service.check_all()

    assert report.total_checked == 3  # 1 page + 1 doc + 1 link


@pytest.mark.asyncio
async def test_check_all_empty_system(
    service, page_repo, doc_repo, inconsistency_repo
):
    page_repo.list_all_urls.return_value = []
    page_repo.list_all_links.return_value = []
    doc_repo.list_done.return_value = []

    report = await service.check_all()

    assert report.total_checked == 0
    assert report.healthy == 0
    assert report.issues_found == 0
    assert report.new_inconsistencies == 0
    assert report.auto_resolved == 0


@pytest.mark.asyncio
async def test_check_all_aggregates_issues(
    service, page_repo, doc_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    page_repo.list_all_links.return_value = [make_link()]
    doc_repo.list_done.return_value = [make_doc()]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=404
    )

    report = await service.check_all()

    assert report.total_checked == 3
    assert report.issues_found == 3
    assert report.new_inconsistencies == 3


@pytest.mark.asyncio
async def test_check_all_aggregates_by_type(
    service, page_repo, doc_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page()]
    page_repo.list_all_links.return_value = [make_link()]
    doc_repo.list_done.return_value = [make_doc()]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=404
    )

    report = await service.check_all()

    assert report.by_type.get("page_not_accessible", 0) >= 1
    assert report.by_type.get("broken_link", 0) >= 1
    assert report.by_type.get("document_not_found", 0) >= 1


@pytest.mark.asyncio
async def test_check_all_duration_is_positive(
    service, page_repo, doc_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = []
    page_repo.list_all_links.return_value = []
    doc_repo.list_done.return_value = []

    report = await service.check_all()

    assert report.duration_seconds >= 0


# ══════════════════════════════════════════════════════════════════════════════
# Gerenciamento de inconsistências
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_inconsistencies_delegates_to_repo(service, inconsistency_repo):
    inc = make_inconsistency()
    inconsistency_repo.list_by_status.return_value = [inc]

    result = await service.get_inconsistencies()

    inconsistency_repo.list_by_status.assert_called_once_with(
        status="open",
        resource_type=None,
        severity=None,
        limit=50,
        offset=0,
    )
    assert result == [inc]


@pytest.mark.asyncio
async def test_get_inconsistencies_with_filters(service, inconsistency_repo):
    inconsistency_repo.list_by_status.return_value = []

    await service.get_inconsistencies(
        status="acknowledged",
        resource_type="document",
        severity="critical",
        limit=10,
        offset=5,
    )

    inconsistency_repo.list_by_status.assert_called_once_with(
        status="acknowledged",
        resource_type="document",
        severity="critical",
        limit=10,
        offset=5,
    )


@pytest.mark.asyncio
async def test_resolve_inconsistency_calls_update_status(service, inconsistency_repo):
    await service.resolve_inconsistency(
        inconsistency_id=42,
        resolution_note="Problema corrigido",
        resolved_by="admin@tre-pi.jus.br",
    )

    inconsistency_repo.update_status.assert_called_once_with(
        42,
        "resolved",
        resolved_by="admin@tre-pi.jus.br",
        resolution_note="Problema corrigido",
    )


@pytest.mark.asyncio
async def test_acknowledge_inconsistency_calls_update_status(service, inconsistency_repo):
    await service.acknowledge_inconsistency(inconsistency_id=7)

    inconsistency_repo.update_status.assert_called_once_with(7, "acknowledged")


@pytest.mark.asyncio
async def test_ignore_inconsistency_calls_update_status(service, inconsistency_repo):
    await service.ignore_inconsistency(
        inconsistency_id=15, reason="Link externo fora do controle"
    )

    inconsistency_repo.update_status.assert_called_once_with(
        15,
        "ignored",
        resolution_note="Link externo fora do controle",
    )


# ══════════════════════════════════════════════════════════════════════════════
# Contratos de DataInconsistency gerada
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_page_inconsistency_has_correct_fields(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page(PAGE_URL, "Transparência")]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=404
    )

    await service.check_pages()

    _, _, inc = inconsistency_repo.upsert.call_args[0]
    assert inc.resource_type == "page"
    assert inc.resource_url == PAGE_URL
    assert inc.resource_title == "Transparência"
    assert inc.parent_page_url is None
    assert inc.detected_by == "health_check"
    assert inc.status == "open"
    assert inc.retry_count == 0
    assert inc.resolved_at is None


@pytest.mark.asyncio
async def test_link_inconsistency_has_correct_resource_type(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_links.return_value = [make_link(LINK_URL, parent=PAGE_URL)]
    downloader.check_accessible.return_value = make_access_result(
        url=LINK_URL, accessible=False, status_code=404
    )

    await service.check_links()

    _, _, inc = inconsistency_repo.upsert.call_args[0]
    assert inc.resource_type == "link"


@pytest.mark.asyncio
async def test_document_not_found_detail_contains_http_status(
    service, doc_repo, downloader, inconsistency_repo
):
    doc_repo.list_done.return_value = [make_doc()]
    downloader.check_accessible.return_value = make_access_result(
        url=DOC_URL, accessible=False, status_code=404
    )

    await service.check_documents()

    _, _, inc = inconsistency_repo.upsert.call_args[0]
    assert "404" in inc.detail


@pytest.mark.asyncio
async def test_upsert_called_with_correct_url_and_type(
    service, page_repo, downloader, inconsistency_repo
):
    page_repo.list_all_urls.return_value = [make_page(PAGE_URL)]
    downloader.check_accessible.return_value = make_access_result(
        accessible=False, status_code=404
    )

    await service.check_pages()

    resource_url, inc_type, _ = inconsistency_repo.upsert.call_args[0]
    assert resource_url == PAGE_URL
    assert inc_type == "page_not_accessible"
