"""Integration tests: PostgresSearchRepository — Etapa 5 (TDD RED → GREEN).

Requer Docker disponível (testcontainers sobe PostgreSQL automaticamente).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.adapters.outbound.postgres.connection import create_pool
from app.adapters.outbound.postgres.search_repo import PostgresSearchRepository
from app.config.settings import Settings
from app.domain.value_objects.search_result import ChunkMatch, PageMatch

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_alembic_config(database_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


@pytest.fixture(scope="module")
def run_migrations(pg_settings: Settings) -> None:  # type: ignore[misc]
    """Aplica migrations uma vez por módulo de teste."""
    cfg = _make_alembic_config(pg_settings.database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture
async def pool(pg_settings: Settings, run_migrations: None):  # type: ignore[misc]
    """Pool asyncpg por teste, com tabelas truncadas para isolamento."""
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
    return PostgresSearchRepository(pool)


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _insert_page(
    conn,  # type: ignore[no-untyped-def]
    url: str,
    title: str,
    description: str | None = None,
    category: str | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO pages (url, title, description, category, content_type, depth)
        VALUES ($1, $2, $3, $4, 'page', 1)
        """,
        url,
        title,
        description,
        category,
    )


async def _insert_content_and_chunk(
    conn,  # type: ignore[no-untyped-def]
    document_url: str,
    chunk_text: str,
    section_title: str | None = None,
) -> None:
    await conn.execute(
        "INSERT INTO document_contents (document_url, document_title, document_type)"
        " VALUES ($1, $2, 'pdf')",
        document_url,
        "Doc Teste",
    )
    await conn.execute(
        """
        INSERT INTO document_chunks (document_url, chunk_index, chunk_text, section_title)
        VALUES ($1, 0, $2, $3)
        """,
        document_url,
        chunk_text,
        section_title,
    )


async def _insert_content_and_table(
    conn,  # type: ignore[no-untyped-def]
    document_url: str,
    caption: str,
    search_text: str,
) -> None:
    await conn.execute(
        "INSERT INTO document_contents (document_url, document_title, document_type)"
        " VALUES ($1, $2, 'pdf') ON CONFLICT (document_url) DO NOTHING",
        document_url,
        "Doc Teste",
    )
    await conn.execute(
        """
        INSERT INTO document_tables
            (document_url, table_index, headers, rows, caption, search_text)
        VALUES ($1, 0, '["Coluna A"]'::jsonb, '[["valor"]]'::jsonb, $2, $3)
        """,
        document_url,
        caption,
        search_text,
    )


# ─── search_pages ─────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_search_pages_retorna_lista_vazia_sem_dados(repo: PostgresSearchRepository) -> None:
    """search_pages deve retornar [] quando não há páginas."""
    results = await repo.search_pages("licitacao")
    assert results == []


@pytest.mark.integration
async def test_search_pages_retorna_pagina_compativel(pool, repo: PostgresSearchRepository) -> None:
    """search_pages deve retornar páginas cujo search_vector bate com a query."""
    async with pool.acquire() as conn:
        await _insert_page(
            conn,
            "https://www.tre-pi.jus.br/servidores",
            "Servidores Publicos Remuneracao",
            description="Relatorio de servidores publicos e remuneracao",
        )
    results = await repo.search_pages("servidores")
    assert len(results) >= 1
    assert isinstance(results[0], PageMatch)
    assert results[0].page.url == "https://www.tre-pi.jus.br/servidores"
    assert results[0].score > 0


@pytest.mark.integration
async def test_search_pages_respeita_top_k(pool, repo: PostgresSearchRepository) -> None:
    """search_pages deve respeitar o parâmetro top_k."""
    async with pool.acquire() as conn:
        for i in range(5):
            await _insert_page(
                conn,
                f"https://www.tre-pi.jus.br/contrato-{i}",
                f"Contrato Administrativo Publico {i}",
                description="Documento de contrato administrativo publico",
            )
    results = await repo.search_pages("contrato", top_k=2)
    assert len(results) <= 2


@pytest.mark.integration
async def test_search_pages_query_vazia_retorna_lista_vazia(repo: PostgresSearchRepository) -> None:
    """search_pages com query vazia não deve disparar SQL."""
    results = await repo.search_pages("")
    assert results == []


# ─── search_chunks ────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_search_chunks_retorna_lista_vazia_sem_dados(repo: PostgresSearchRepository) -> None:
    """search_chunks deve retornar [] quando não há chunks."""
    results = await repo.search_chunks("contrato")
    assert results == []


@pytest.mark.integration
async def test_search_chunks_retorna_chunk_compativel(pool, repo: PostgresSearchRepository) -> None:
    """search_chunks deve retornar chunks cujo search_vector bate com a query."""
    async with pool.acquire() as conn:
        await _insert_content_and_chunk(
            conn,
            "https://www.tre-pi.jus.br/relatorio.pdf",
            "Relatorio financeiro anual consolidado exercicio",
            section_title="Relatorio Financeiro",
        )
    results = await repo.search_chunks("financeiro")
    assert len(results) >= 1
    assert isinstance(results[0], ChunkMatch)
    assert results[0].score > 0
    assert results[0].chunk.document_url == "https://www.tre-pi.jus.br/relatorio.pdf"


@pytest.mark.integration
async def test_search_chunks_query_vazia_retorna_lista_vazia(repo: PostgresSearchRepository) -> None:
    """search_chunks com query vazia não deve disparar SQL."""
    results = await repo.search_chunks("   ")
    assert results == []


# ─── search_tables ────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_search_tables_retorna_lista_vazia_sem_dados(repo: PostgresSearchRepository) -> None:
    """search_tables deve retornar [] quando não há tabelas."""
    results = await repo.search_tables("folha")
    assert results == []


@pytest.mark.integration
async def test_search_tables_encontra_por_caption(pool, repo: PostgresSearchRepository) -> None:
    """search_tables deve encontrar tabela pela caption."""
    async with pool.acquire() as conn:
        await _insert_content_and_table(
            conn,
            "https://www.tre-pi.jus.br/folha.pdf",
            caption="Folha de Pagamento Mensal",
            search_text="servidores remuneração folha",
        )
    results = await repo.search_tables("Folha")
    assert len(results) >= 1
    assert results[0].caption == "Folha de Pagamento Mensal"


@pytest.mark.integration
async def test_search_tables_query_vazia_retorna_lista_vazia(repo: PostgresSearchRepository) -> None:
    """search_tables com query vazia deve retornar []."""
    results = await repo.search_tables("")
    assert results == []


# ─── get_categories ───────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_categories_retorna_lista_vazia_sem_dados(repo: PostgresSearchRepository) -> None:
    """get_categories deve retornar [] quando não há páginas com categoria."""
    cats = await repo.get_categories()
    assert cats == []


@pytest.mark.integration
async def test_get_categories_agrupa_por_categoria(pool, repo: PostgresSearchRepository) -> None:
    """get_categories deve agregar categorias distintas com contagem."""
    async with pool.acquire() as conn:
        for i in range(3):
            await _insert_page(
                conn,
                f"https://www.tre-pi.jus.br/transp/{i}",
                f"Transparência {i}",
                category="transparencia",
            )
        await _insert_page(
            conn,
            "https://www.tre-pi.jus.br/eleicoes/1",
            "Eleições",
            category="eleicoes",
        )
    cats = await repo.get_categories()
    assert len(cats) >= 2
    names = [c["name"] for c in cats]
    assert "transparencia" in names
    assert "eleicoes" in names
    transp = next(c for c in cats if c["name"] == "transparencia")
    assert transp["count"] == 3


# ─── get_stats ────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_stats_retorna_zeros_sem_dados(repo: PostgresSearchRepository) -> None:
    """get_stats deve retornar contagens zeradas quando banco está vazio."""
    stats = await repo.get_stats()
    assert stats["total_pages"] == 0
    assert stats["total_chunks"] == 0
    assert stats["total_tables"] == 0
    assert stats["total_documents"] == 0


@pytest.mark.integration
async def test_get_stats_reflete_dados_inseridos(pool, repo: PostgresSearchRepository) -> None:
    """get_stats deve refletir os registros presentes no banco."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://www.tre-pi.jus.br/p1", "Página 1", category="transp")
        await _insert_page(conn, "https://www.tre-pi.jus.br/p2", "Página 2", category="transp")
        await _insert_content_and_chunk(conn, "https://www.tre-pi.jus.br/doc.pdf", "Texto do chunk")
    stats = await repo.get_stats()
    assert stats["total_pages"] == 2
    assert stats["total_chunks"] == 1
