"""Testes E2E — DataHealthCheckService (Etapa 9 Pipeline V2).

Critérios de aceite:
- Health check detecta página 404 → inconsistência criada em data_inconsistencies
- Health check detecta link quebrado → inconsistência criada
- Recurso que volta a funcionar → inconsistência auto-resolvida
- check_all() agrega resultados de páginas, documentos e links
"""

from __future__ import annotations

import asyncpg
import pytest

from app.adapters.outbound.postgres.document_repo import PostgresDocumentRepository
from app.adapters.outbound.postgres.inconsistency_repo import PostgresInconsistencyRepository
from app.adapters.outbound.postgres.page_repo import PostgresPageRepository
from app.domain.ports.outbound.page_repository import CrawledDocument, CrawledLink, CrawledPage
from app.domain.services.data_health_check_service import DataHealthCheckService
from tests.e2e.conftest import FakeDownloadGateway, docker_required

PAGE_OK = "https://www.tre-pi.jus.br/transparencia"
PAGE_404 = "https://www.tre-pi.jus.br/pagina-inexistente"
LINK_OK = "https://www.tre-pi.jus.br/licitacoes"
LINK_BROKEN = "https://www.tre-pi.jus.br/link-quebrado"
DOC_OK = "https://www.tre-pi.jus.br/docs/relatorio.pdf"
DOC_404 = "https://www.tre-pi.jus.br/docs/arquivo-removido.pdf"


# ─── Helpers de seed ──────────────────────────────────────────────────────────


async def _insert_page_direct(
    conn: asyncpg.Connection, url: str, title: str = "Página"
) -> None:
    await conn.execute(
        "INSERT INTO pages (url, title) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        url, title,
    )


async def _insert_page_with_link(pool: asyncpg.Pool, page_url: str, link_url: str) -> None:
    """Insere página com link interno via PageRepository."""
    repo = PostgresPageRepository(pool)
    await repo.upsert_page(
        CrawledPage(
            url=page_url,
            title="Página com link",
            category="Transparência",
            internal_links=[CrawledLink(target_url=link_url, link_title="Link teste")],
        )
    )


async def _insert_document_done(
    conn: asyncpg.Connection, doc_url: str, page_url: str
) -> None:
    await conn.execute(
        """
        INSERT INTO documents (page_url, document_url, document_title, document_type, processing_status)
        VALUES ($1, $2, $3, 'pdf', 'done')
        ON CONFLICT DO NOTHING
        """,
        page_url, doc_url, "Documento de Teste",
    )


def _make_health_service(
    pool: asyncpg.Pool,
    downloader: FakeDownloadGateway | None = None,
) -> DataHealthCheckService:
    return DataHealthCheckService(
        downloader=downloader or FakeDownloadGateway(),
        page_repo=PostgresPageRepository(pool),
        doc_repo=PostgresDocumentRepository(pool),
        inconsistency_repo=PostgresInconsistencyRepository(pool),
        request_delay_ms=0.0,  # sem delay nos testes
    )


# ─── Testes: check_pages ──────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_check_pages_detecta_pagina_inacessivel(
    pool_e2e: asyncpg.Pool,
) -> None:
    """check_pages() deve registrar inconsistência para página que retorna 404."""
    downloader = FakeDownloadGateway(broken_urls={PAGE_404})

    async with pool_e2e.acquire() as conn:
        await _insert_page_direct(conn, PAGE_OK, "Página OK")
        await _insert_page_direct(conn, PAGE_404, "Página 404")

    svc = _make_health_service(pool_e2e, downloader=downloader)
    report = await svc.check_pages()

    assert report.total_checked == 2
    assert report.healthy == 1
    assert report.issues_found == 1
    assert report.new_inconsistencies == 1

    async with pool_e2e.acquire() as conn:
        cnt = await conn.fetchval(
            """
            SELECT COUNT(*) FROM data_inconsistencies
            WHERE resource_url = $1 AND inconsistency_type = 'page_not_accessible'
            """,
            PAGE_404,
        )
    assert cnt == 1


@docker_required
@pytest.mark.integration
async def test_check_pages_inconsistencia_tem_detected_by_health_check(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Inconsistências geradas pelo health check devem ter detected_by='health_check'."""
    downloader = FakeDownloadGateway(broken_urls={PAGE_404})

    async with pool_e2e.acquire() as conn:
        await _insert_page_direct(conn, PAGE_404, "Página Quebrada")

    svc = _make_health_service(pool_e2e, downloader=downloader)
    await svc.check_pages()

    async with pool_e2e.acquire() as conn:
        detected_by = await conn.fetchval(
            "SELECT detected_by FROM data_inconsistencies WHERE resource_url = $1",
            PAGE_404,
        )
    assert detected_by == "health_check"


@docker_required
@pytest.mark.integration
async def test_check_pages_auto_resolve_quando_recurso_volta_a_funcionar(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Página que volta a funcionar deve ter sua inconsistência resolvida automaticamente."""
    downloader = FakeDownloadGateway(broken_urls={PAGE_404})

    async with pool_e2e.acquire() as conn:
        await _insert_page_direct(conn, PAGE_404, "Página Temporariamente Fora")

    svc = _make_health_service(pool_e2e, downloader=downloader)

    # Primeira verificação: página inacessível → inconsistência criada
    report1 = await svc.check_pages()
    assert report1.new_inconsistencies == 1

    # Página "se recupera"
    downloader.set_accessible(PAGE_404)

    # Segunda verificação: página acessível → inconsistência auto-resolvida
    report2 = await svc.check_pages()
    assert report2.auto_resolved == 1
    assert report2.new_inconsistencies == 0

    async with pool_e2e.acquire() as conn:
        status = await conn.fetchval(
            """
            SELECT status FROM data_inconsistencies
            WHERE resource_url = $1 AND inconsistency_type = 'page_not_accessible'
            ORDER BY id DESC LIMIT 1
            """,
            PAGE_404,
        )
    assert status == "resolved"


@docker_required
@pytest.mark.integration
async def test_check_pages_nao_duplica_inconsistencia_aberta(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Verificações repetidas da mesma página quebrada não devem duplicar inconsistências."""
    downloader = FakeDownloadGateway(broken_urls={PAGE_404})

    async with pool_e2e.acquire() as conn:
        await _insert_page_direct(conn, PAGE_404, "Página Quebrada")

    svc = _make_health_service(pool_e2e, downloader=downloader)

    await svc.check_pages()
    await svc.check_pages()
    await svc.check_pages()

    async with pool_e2e.acquire() as conn:
        cnt = await conn.fetchval(
            """
            SELECT COUNT(*) FROM data_inconsistencies
            WHERE resource_url = $1 AND status = 'open'
            """,
            PAGE_404,
        )
    assert cnt == 1, "Não deve haver inconsistências duplicadas para o mesmo recurso"


# ─── Testes: check_links ──────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_check_links_detecta_link_quebrado(
    pool_e2e: asyncpg.Pool,
) -> None:
    """check_links() deve criar inconsistência 'broken_link' para link inacessível."""
    downloader = FakeDownloadGateway(broken_urls={LINK_BROKEN})

    await _insert_page_with_link(pool_e2e, PAGE_OK, LINK_OK)
    await _insert_page_with_link(pool_e2e, PAGE_404, LINK_BROKEN)

    svc = _make_health_service(pool_e2e, downloader=downloader)
    report = await svc.check_links()

    assert report.total_checked == 2
    assert report.healthy == 1
    assert report.issues_found == 1

    async with pool_e2e.acquire() as conn:
        inc = await conn.fetchrow(
            """
            SELECT resource_url, resource_type, inconsistency_type
            FROM data_inconsistencies
            WHERE resource_url = $1
            """,
            LINK_BROKEN,
        )
    assert inc is not None
    assert inc["inconsistency_type"] == "broken_link"
    assert inc["resource_type"] == "link"


@docker_required
@pytest.mark.integration
async def test_check_links_auto_resolve_link_restaurado(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Link que volta a funcionar deve ter inconsistência auto-resolvida."""
    downloader = FakeDownloadGateway(broken_urls={LINK_BROKEN})

    await _insert_page_with_link(pool_e2e, PAGE_OK, LINK_BROKEN)

    svc = _make_health_service(pool_e2e, downloader=downloader)

    # 1ª verificação: link quebrado
    await svc.check_links()

    # Link "se recupera"
    downloader.set_accessible(LINK_BROKEN)

    # 2ª verificação: auto-resolve
    report2 = await svc.check_links()

    assert report2.auto_resolved == 1

    async with pool_e2e.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM data_inconsistencies WHERE resource_url = $1",
            LINK_BROKEN,
        )
    assert status == "resolved"


# ─── Testes: check_all ────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_check_all_agrega_resultados_de_paginas_documentos_e_links(
    pool_e2e: asyncpg.Pool,
) -> None:
    """check_all() deve agregar resultados das três verificações."""
    downloader = FakeDownloadGateway(broken_urls={PAGE_404, DOC_404})

    async with pool_e2e.acquire() as conn:
        await _insert_page_direct(conn, PAGE_OK, "Página OK")
        await _insert_page_direct(conn, PAGE_404, "Página Quebrada")
        await _insert_document_done(conn, DOC_OK, PAGE_OK)
        await _insert_document_done(conn, DOC_404, PAGE_OK)

    svc = _make_health_service(pool_e2e, downloader=downloader)
    report = await svc.check_all()

    # 2 páginas + 2 documentos (sem links)
    assert report.total_checked == 4
    assert report.issues_found == 2  # 1 página + 1 documento quebrado
    assert report.new_inconsistencies == 2
