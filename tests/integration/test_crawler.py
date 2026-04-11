"""Integration tests: PostgresPageRepository + CrawlerCLI — Etapa 11 (TDD RED → GREEN).

Prova que o upsert é idempotente: re-executar o crawler com os mesmos dados
não cria duplicatas no banco PostgreSQL.

Requer Docker disponível (testcontainers sobe PostgreSQL automaticamente).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from alembic import command
from alembic.config import Config

from app.adapters.inbound.cli.crawler import (
    CrawledDocument,
    CrawledLink,
    CrawledPage,
    CrawlerCLI,
    CrawlerConfig,
    PageExtractor,
    _classify_content_type,
    _normalize_url,
    _slug_to_title,
)
from app.adapters.outbound.postgres.connection import create_pool
from app.adapters.outbound.postgres.page_repo import PostgresPageRepository
from app.config.settings import Settings
from app.domain.ports.outbound.page_repository import (
    CrawledDocument as DomainDoc,
    CrawledLink as DomainLink,
    CrawledPage as DomainPage,
)

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ─── Fixtures ─────────────────────────────────────────────────────────────────


def _make_alembic_config(database_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture(scope="module")
def run_migrations(pg_settings: Settings) -> None:  # type: ignore[misc]
    cfg = _make_alembic_config(pg_settings.database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture
async def pool(pg_settings: Settings, run_migrations: None):  # type: ignore[misc]
    p = await create_pool(pg_settings)
    async with p.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE TABLE query_logs, chat_messages, chat_sessions,
                document_tables, document_chunks, document_contents,
                page_links, navigation_tree, documents, pages
            RESTART IDENTITY CASCADE
            """
        )
    yield p
    await p.close()


@pytest.fixture
async def repo(pool):  # type: ignore[misc]
    return PostgresPageRepository(pool)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _simple_page(
    url: str = "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria",
    title: str = "Ouvidoria",
) -> DomainPage:
    return DomainPage(
        url=url,
        title=title,
        description="Página da Ouvidoria do TRE-PI",
        main_content="Conteúdo principal da ouvidoria",
        content_summary="Resumo da ouvidoria",
        category="Ouvidoria",
        subcategory="",
        content_type="page",
        depth=2,
        parent_url="https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas",
        breadcrumb=[
            {"title": "Transparência", "url": "https://www.tre-pi.jus.br/transparencia"},
            {"title": "Ouvidoria", "url": url},
        ],
        tags=["ouvidoria", "sic"],
    )


# ─── Testes: Utilitários puros (sem banco) ────────────────────────────────────


class TestUtilitarios:
    def test_normalize_url_remove_trailing_slash(self) -> None:
        assert _normalize_url("https://www.tre-pi.jus.br/path/") == "https://www.tre-pi.jus.br/path"

    def test_normalize_url_preserva_sem_barra(self) -> None:
        url = "https://www.tre-pi.jus.br/transparencia"
        assert _normalize_url(url) == url

    def test_slug_to_title_mapeado(self) -> None:
        assert _slug_to_title("ouvidoria") == "Ouvidoria"

    def test_slug_to_title_nao_mapeado(self) -> None:
        assert _slug_to_title("relatorio-anual") == "Relatorio Anual"

    def test_classify_pdf_por_extensao(self) -> None:
        assert _classify_content_type("https://x.br/doc.pdf", "") == "pdf"

    def test_classify_csv_por_extensao(self) -> None:
        assert _classify_content_type("https://x.br/dados.csv", "") == "csv"

    def test_classify_page_por_content_type(self) -> None:
        assert _classify_content_type("https://x.br/page", "text/html; charset=utf-8") == "page"

    def test_classify_pdf_por_content_type(self) -> None:
        assert _classify_content_type("https://x.br/doc", "application/pdf") == "pdf"


# ─── Testes: PostgresPageRepository — upsert idempotente ─────────────────────


class TestPostgresPageRepositoryUpsert:
    @pytest.mark.integration
    async def test_upsert_page_insere_nova_pagina(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        page = _simple_page()
        await repo.upsert_page(page)

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM pages WHERE url = $1", page.url)

        assert row is not None
        assert row["title"] == "Ouvidoria"
        assert row["category"] == "Ouvidoria"
        assert row["depth"] == 2

    @pytest.mark.integration
    async def test_upsert_page_idempotente_nao_duplica(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        """Re-executar upsert com a mesma URL não cria nova linha."""
        page = _simple_page()
        await repo.upsert_page(page)
        await repo.upsert_page(page)  # segunda vez

        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM pages WHERE url = $1", page.url)

        assert count == 1

    @pytest.mark.integration
    async def test_upsert_page_atualiza_titulo(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        """Segunda chamada com dados diferentes atualiza os campos."""
        page = _simple_page(title="Ouvidoria v1")
        await repo.upsert_page(page)

        page_v2 = _simple_page(title="Ouvidoria v2")
        await repo.upsert_page(page_v2)

        async with pool.acquire() as conn:
            row = await conn.fetchrow("SELECT title FROM pages WHERE url = $1", page.url)
            count = await conn.fetchval("SELECT COUNT(*) FROM pages WHERE url = $1", page.url)

        assert count == 1
        assert row["title"] == "Ouvidoria v2"

    @pytest.mark.integration
    async def test_upsert_page_com_documentos(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        page = _simple_page()
        page.documents = [
            DomainDoc(
                document_url="https://www.tre-pi.jus.br/doc/relatorio.pdf",
                document_title="Relatório Anual 2024",
                document_type="pdf",
            ),
            DomainDoc(
                document_url="https://www.tre-pi.jus.br/doc/dados.csv",
                document_title="Dados Abertos",
                document_type="csv",
            ),
        ]
        await repo.upsert_page(page)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT document_url, document_type FROM documents WHERE page_url = $1",
                page.url,
            )

        assert len(rows) == 2
        types = {r["document_type"] for r in rows}
        assert types == {"pdf", "csv"}

    @pytest.mark.integration
    async def test_upsert_page_documentos_idempotente(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        """Re-executar upsert com os mesmos documentos não cria duplicatas."""
        page = _simple_page()
        page.documents = [
            DomainDoc(
                document_url="https://www.tre-pi.jus.br/doc/relatorio.pdf",
                document_title="Relatório",
                document_type="pdf",
            )
        ]
        await repo.upsert_page(page)
        await repo.upsert_page(page)  # segunda vez

        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM documents WHERE page_url = $1", page.url
            )

        assert count == 1

    @pytest.mark.integration
    async def test_upsert_page_documentos_atualiza_titulo(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        """Segunda chamada com título diferente atualiza o documento."""
        page = _simple_page()
        page.documents = [
            DomainDoc(
                document_url="https://www.tre-pi.jus.br/doc/relatorio.pdf",
                document_title="Título Original",
                document_type="pdf",
            )
        ]
        await repo.upsert_page(page)

        page.documents[0].document_title = "Título Atualizado"
        await repo.upsert_page(page)

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT document_title FROM documents WHERE page_url = $1 AND document_url = $2",
                page.url,
                "https://www.tre-pi.jus.br/doc/relatorio.pdf",
            )
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM documents WHERE page_url = $1", page.url
            )

        assert count == 1
        assert row["document_title"] == "Título Atualizado"

    @pytest.mark.integration
    async def test_upsert_page_com_links_internos(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        page = _simple_page()
        page.internal_links = [
            DomainLink(
                target_url="https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria/formulario",
                link_title="Formulário de contato",
            ),
        ]
        await repo.upsert_page(page)

        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM page_links WHERE source_url = $1", page.url
            )

        assert count == 1

    @pytest.mark.integration
    async def test_upsert_page_links_idempotente(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        """Re-executar upsert com os mesmos links não cria duplicatas."""
        page = _simple_page()
        page.internal_links = [
            DomainLink(
                target_url="https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria/formulario",
                link_title="Formulário",
            ),
        ]
        await repo.upsert_page(page)
        await repo.upsert_page(page)  # segunda vez

        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM page_links WHERE source_url = $1", page.url
            )

        assert count == 1

    @pytest.mark.integration
    async def test_upsert_page_com_parent_cria_navigation_tree(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        page = _simple_page()  # parent_url definido na fixture
        await repo.upsert_page(page)

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT child_title FROM navigation_tree WHERE child_url = $1", page.url
            )

        assert row is not None
        assert row["child_title"] == "Ouvidoria"

    @pytest.mark.integration
    async def test_upsert_page_navigation_tree_idempotente(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        """Re-executar não duplica entradas na navigation_tree."""
        page = _simple_page()
        await repo.upsert_page(page)
        await repo.upsert_page(page)

        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM navigation_tree WHERE child_url = $1", page.url
            )

        assert count == 1

    @pytest.mark.integration
    async def test_count_pages_retorna_zero_sem_dados(self, repo: PostgresPageRepository) -> None:
        count = await repo.count_pages()
        assert count == 0

    @pytest.mark.integration
    async def test_count_pages_retorna_total_correto(self, repo: PostgresPageRepository) -> None:
        urls = [
            "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria",
            "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/governanca",
            "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/contabilidade",
        ]
        for url in urls:
            await repo.upsert_page(
                DomainPage(url=url, title=url.split("/")[-1], content_type="page", depth=2)
            )

        count = await repo.count_pages()
        assert count == 3

    @pytest.mark.integration
    async def test_count_pages_idempotente_com_upserts_repetidos(self, repo: PostgresPageRepository) -> None:  # type: ignore[no-untyped-def]
        """Upserts repetidos não aumentam o count."""
        page = _simple_page()
        for _ in range(5):
            await repo.upsert_page(page)

        count = await repo.count_pages()
        assert count == 1

    @pytest.mark.integration
    async def test_upsert_pagina_sem_parent_nao_cria_navigation_entry(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        """Página raiz sem parent_url não insere na navigation_tree."""
        page = DomainPage(
            url="https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas",
            title="Transparência",
            content_type="page",
            depth=0,
        )
        await repo.upsert_page(page)

        async with pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM navigation_tree WHERE child_url = $1",
                page.url,
            )

        assert count == 0

    @pytest.mark.integration
    async def test_upsert_pagina_com_tags_lista_vazia(self, repo: PostgresPageRepository, pool) -> None:  # type: ignore[no-untyped-def]
        page = DomainPage(
            url="https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/colegiados",
            title="Colegiados",
            content_type="page",
            depth=2,
            tags=[],
        )
        await repo.upsert_page(page)

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT tags FROM pages WHERE url = $1", page.url
            )

        assert row is not None
        assert list(row["tags"]) == []


# ─── Testes: CrawlerCLI com mock do PageRepository ───────────────────────────


class TestCrawlerCLI:
    async def test_run_full_chama_upsert_para_cada_pagina(self) -> None:
        """CrawlerCLI chama upsert_page para cada URL descoberta."""
        fake_repo = AsyncMock()
        fake_repo.count_pages.return_value = 0

        fake_urls = [
            {
                "url": "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria",
                "content_type": "page",
                "depth": 2,
                "parent_url": "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas",
            }
        ]

        with (
            patch(
                "app.adapters.inbound.cli.crawler.discover_all_urls",
                new=AsyncMock(return_value=fake_urls),
            ),
            patch.object(PageExtractor, "extract", new=AsyncMock(return_value=CrawledPage(url="https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria", title="Ouvidoria"))),
        ):
            crawler = CrawlerCLI(fake_repo, CrawlerConfig(delay=0))
            stats = await crawler.run_full()

        fake_repo.upsert_page.assert_called_once()
        assert stats.pages_upserted == 1
        assert stats.errors == 0

    async def test_run_full_conta_erros_de_upsert(self) -> None:
        """Erros de upsert são contados mas não interrompem o crawler."""
        fake_repo = AsyncMock()
        fake_repo.upsert_page.side_effect = RuntimeError("DB error")

        fake_urls = [
            {
                "url": "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria",
                "content_type": "page",
                "depth": 2,
            }
        ]

        with (
            patch(
                "app.adapters.inbound.cli.crawler.discover_all_urls",
                new=AsyncMock(return_value=fake_urls),
            ),
            patch.object(PageExtractor, "extract", new=AsyncMock(return_value=CrawledPage(url="https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria", title="Ouvidoria"))),
        ):
            crawler = CrawlerCLI(fake_repo, CrawlerConfig(delay=0))
            stats = await crawler.run_full()

        assert stats.pages_upserted == 0
        assert stats.errors == 1

    async def test_run_stats_retorna_total_pages(self) -> None:
        fake_repo = AsyncMock()
        fake_repo.count_pages.return_value = 42

        crawler = CrawlerCLI(fake_repo)
        result = await crawler.run_stats()

        assert result["total_pages"] == 42

    async def test_run_update_usa_sitemap(self) -> None:
        """run_update usa apenas sitemap, não BFS completo."""
        fake_repo = AsyncMock()

        fake_urls = [
            {
                "url": "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria",
                "content_type": "page",
                "depth": 2,
            }
        ]

        with (
            patch(
                "app.adapters.inbound.cli.crawler.fetch_sitemap_urls",
                new=AsyncMock(return_value=fake_urls),
            ),
            patch.object(PageExtractor, "extract", new=AsyncMock(return_value=CrawledPage(url="https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas/ouvidoria", title="Ouvidoria"))),
        ):
            crawler = CrawlerCLI(fake_repo, CrawlerConfig(delay=0))
            stats = await crawler.run_update()

        assert stats.urls_discovered == 1
        assert stats.pages_upserted == 1
