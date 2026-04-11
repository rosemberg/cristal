"""Testes de integração — DocumentRepository: list_pending, list_errors, update_status (Etapa 3 Pipeline V2).

TDD RED: escrito antes da implementação.

Critérios de aceite:
- list_pending() retorna documentos com processing_status = 'pending'
- list_pending(limit) respeita o limite
- list_errors() retorna documentos com processing_status = 'error'
- update_status() atualiza status e timestamp
- update_status() com mensagem de erro persiste processing_error
- Status inválido em update_status() lança ValueError
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.adapters.outbound.postgres.connection import create_pool
from app.adapters.outbound.postgres.document_repo import PostgresDocumentRepository
from app.config.settings import Settings
from tests.integration.conftest import docker_required

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _make_alembic_config(database_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


# ─── Fixtures ─────────────────────────────────────────────────────────────────


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
            TRUNCATE TABLE data_inconsistencies, query_logs, chat_messages, chat_sessions,
                document_tables, document_chunks, document_contents,
                page_links, navigation_tree, documents, pages
            RESTART IDENTITY CASCADE
            """
        )
    yield p
    await p.close()


@pytest.fixture
async def repo(pool):  # type: ignore[misc]
    return PostgresDocumentRepository(pool)


# ─── Helpers ──────────────────────────────────────────────────────────────────

PAGE_URL = "https://www.tre-pi.jus.br/transparencia"
DOC_URL = "https://www.tre-pi.jus.br/transparencia/relatorio.pdf"
DOC_URL_2 = "https://www.tre-pi.jus.br/transparencia/folha.pdf"
DOC_URL_3 = "https://www.tre-pi.jus.br/transparencia/ata.pdf"


async def _insert_page(conn, url: str = PAGE_URL) -> None:  # type: ignore[no-untyped-def]
    await conn.execute(
        "INSERT INTO pages (url, title, category, content_type, depth) VALUES ($1, $2, $3, 'page', 1)",
        url,
        "Transparência",
        "transparencia",
    )


async def _insert_document(  # type: ignore[no-untyped-def]
    conn,
    doc_url: str = DOC_URL,
    page_url: str = PAGE_URL,
    status: str = "pending",
    title: str = "Relatório",
) -> None:
    await conn.execute(
        """
        INSERT INTO documents (page_url, document_url, document_type, document_title, processing_status)
        VALUES ($1, $2, 'pdf', $3, $4)
        """,
        page_url,
        doc_url,
        title,
        status,
    )


# ─── list_pending ─────────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_list_pending_retorna_lista_vazia_sem_documentos(
    repo: PostgresDocumentRepository,
) -> None:
    docs = await repo.list_pending()
    assert docs == []


@docker_required
@pytest.mark.integration
async def test_list_pending_retorna_documentos_pendentes(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, status="pending")
        await _insert_document(conn, doc_url=DOC_URL_2, status="done")

    docs = await repo.list_pending()
    assert len(docs) == 1
    assert docs[0].document_url == DOC_URL


@docker_required
@pytest.mark.integration
async def test_list_pending_respeita_limit(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        for i in range(5):
            await _insert_document(
                conn,
                doc_url=f"https://www.tre-pi.jus.br/doc-{i}.pdf",
                status="pending",
                title=f"Doc {i}",
            )

    docs = await repo.list_pending(limit=3)
    assert len(docs) == 3


@docker_required
@pytest.mark.integration
async def test_list_pending_ignora_error_e_done(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, status="pending")
        await _insert_document(conn, doc_url=DOC_URL_2, status="error")
        await _insert_document(conn, doc_url=DOC_URL_3, status="done")

    docs = await repo.list_pending()
    assert len(docs) == 1
    assert docs[0].document_url == DOC_URL


# ─── list_errors ──────────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_list_errors_retorna_lista_vazia_sem_erros(
    repo: PostgresDocumentRepository,
) -> None:
    docs = await repo.list_errors()
    assert docs == []


@docker_required
@pytest.mark.integration
async def test_list_errors_retorna_documentos_com_erro(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, status="error")
        await _insert_document(conn, doc_url=DOC_URL_2, status="pending")

    docs = await repo.list_errors()
    assert len(docs) == 1
    assert docs[0].document_url == DOC_URL


@docker_required
@pytest.mark.integration
async def test_list_errors_ignora_pending_e_done(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, status="done")
        await _insert_document(conn, doc_url=DOC_URL_2, status="pending")

    docs = await repo.list_errors()
    assert docs == []


# ─── update_status ────────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_update_status_altera_para_processing(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, status="pending")

    await repo.update_status(DOC_URL, "processing")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT processing_status, processed_at FROM documents WHERE document_url = $1",
            DOC_URL,
        )
    assert row["processing_status"] == "processing"
    assert row["processed_at"] is not None


@docker_required
@pytest.mark.integration
async def test_update_status_altera_para_done(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, status="processing")

    await repo.update_status(DOC_URL, "done")

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT processing_status FROM documents WHERE document_url = $1", DOC_URL
        )
    assert status == "done"


@docker_required
@pytest.mark.integration
async def test_update_status_persiste_mensagem_de_erro(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, status="processing")

    await repo.update_status(DOC_URL, "error", error="HTTP 404: documento não encontrado")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT processing_status, processing_error FROM documents WHERE document_url = $1",
            DOC_URL,
        )
    assert row["processing_status"] == "error"
    assert row["processing_error"] == "HTTP 404: documento não encontrado"


@docker_required
@pytest.mark.integration
async def test_update_status_limpa_erro_ao_retornar_para_pending(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, status="error")
        await conn.execute(
            "UPDATE documents SET processing_error = 'erro anterior' WHERE document_url = $1",
            DOC_URL,
        )

    await repo.update_status(DOC_URL, "pending")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT processing_status, processing_error FROM documents WHERE document_url = $1",
            DOC_URL,
        )
    assert row["processing_status"] == "pending"
    assert row["processing_error"] is None


@docker_required
@pytest.mark.integration
async def test_update_status_invalido_lanca_value_error(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, status="pending")

    with pytest.raises(ValueError, match="(?i)status"):
        await repo.update_status(DOC_URL, "invalido")
