"""Integration tests: PostgresDocumentRepository — Etapa 5 (TDD RED → GREEN).

Requer Docker disponível (testcontainers sobe PostgreSQL automaticamente).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.adapters.outbound.postgres.connection import create_pool
from app.adapters.outbound.postgres.document_repo import PostgresDocumentRepository
from app.config.settings import Settings
from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document_table import DocumentTable
from app.domain.ports.outbound.document_repository import ProcessedDocument

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
    return PostgresDocumentRepository(pool)


# ─── Helpers ──────────────────────────────────────────────────────────────────

PAGE_URL = "https://www.tre-pi.jus.br/transparencia"
DOC_URL = "https://www.tre-pi.jus.br/transparencia/relatorio.pdf"
DOC_URL_2 = "https://www.tre-pi.jus.br/transparencia/folha.pdf"


async def _insert_page(conn, url: str = PAGE_URL, category: str = "transparencia") -> None:  # type: ignore[no-untyped-def]
    await conn.execute(
        "INSERT INTO pages (url, title, category, content_type, depth) VALUES ($1, $2, $3, 'page', 1)",
        url,
        "Transparência",
        category,
    )


async def _insert_document(  # type: ignore[no-untyped-def]
    conn,
    page_url: str = PAGE_URL,
    doc_url: str = DOC_URL,
    doc_type: str = "pdf",
    title: str = "Relatório Anual",
) -> None:
    await conn.execute(
        """
        INSERT INTO documents (page_url, document_url, document_type, document_title)
        VALUES ($1, $2, $3, $4)
        """,
        page_url,
        doc_url,
        doc_type,
        title,
    )


async def _insert_content(conn, doc_url: str = DOC_URL, status: str = "done") -> None:  # type: ignore[no-untyped-def]
    await conn.execute(
        """
        INSERT INTO document_contents
            (document_url, document_title, full_text, num_pages, processing_status)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (document_url) DO NOTHING
        """,
        doc_url,
        "Relatório Anual",
        "Conteúdo do relatório anual.",
        10,
        status,
    )


# ─── find_by_url ──────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_find_by_url_retorna_none_quando_nao_encontrado(
    repo: PostgresDocumentRepository,
) -> None:
    result = await repo.find_by_url("https://www.tre-pi.jus.br/inexistente.pdf")
    assert result is None


@pytest.mark.integration
async def test_find_by_url_retorna_documento(pool, repo: PostgresDocumentRepository) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn)
    doc = await repo.find_by_url(DOC_URL)
    assert doc is not None
    assert doc.document_url == DOC_URL
    assert doc.page_url == PAGE_URL
    assert doc.type == "pdf"
    assert doc.title == "Relatório Anual"
    assert doc.is_processed is False  # sem document_contents


@pytest.mark.integration
async def test_find_by_url_is_processed_quando_em_document_contents(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn)
        await _insert_content(conn, status="done")
    doc = await repo.find_by_url(DOC_URL)
    assert doc is not None
    assert doc.is_processed is True
    assert doc.num_pages == 10


@pytest.mark.integration
async def test_find_by_url_is_processed_false_quando_pendente(
    pool, repo: PostgresDocumentRepository
) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn)
        await _insert_content(conn, status="pending")
    doc = await repo.find_by_url(DOC_URL)
    assert doc is not None
    assert doc.is_processed is False


# ─── list_documents ───────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_list_documents_retorna_lista_vazia(repo: PostgresDocumentRepository) -> None:
    docs = await repo.list_documents()
    assert docs == []


@pytest.mark.integration
async def test_list_documents_retorna_todos(pool, repo: PostgresDocumentRepository) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL)
        await _insert_document(conn, doc_url=DOC_URL_2, title="Folha de Pagamento")
    docs = await repo.list_documents()
    assert len(docs) == 2


@pytest.mark.integration
async def test_list_documents_filtra_por_tipo(pool, repo: PostgresDocumentRepository) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, doc_url=DOC_URL, doc_type="pdf")
        await _insert_document(conn, doc_url=DOC_URL_2, doc_type="csv")
    docs_pdf = await repo.list_documents(doc_type="pdf")
    docs_csv = await repo.list_documents(doc_type="csv")
    assert len(docs_pdf) == 1
    assert docs_pdf[0].type == "pdf"
    assert len(docs_csv) == 1
    assert docs_csv[0].type == "csv"


@pytest.mark.integration
async def test_list_documents_filtra_por_categoria(pool, repo: PostgresDocumentRepository) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn, url=PAGE_URL, category="transparencia")
        other_page = "https://www.tre-pi.jus.br/eleicoes"
        await _insert_page(conn, url=other_page, category="eleicoes")
        await _insert_document(conn, page_url=PAGE_URL, doc_url=DOC_URL)
        await _insert_document(conn, page_url=other_page, doc_url=DOC_URL_2)
    docs = await repo.list_documents(category="transparencia")
    assert len(docs) == 1
    assert docs[0].document_url == DOC_URL


@pytest.mark.integration
async def test_list_documents_paginacao(pool, repo: PostgresDocumentRepository) -> None:
    async with pool.acquire() as conn:
        await _insert_page(conn)
        for i in range(5):
            await _insert_document(
                conn,
                doc_url=f"https://www.tre-pi.jus.br/doc-{i}.pdf",
                title=f"Doc {i}",
            )
    page1 = await repo.list_documents(page=1, size=2)
    page2 = await repo.list_documents(page=2, size=2)
    page3 = await repo.list_documents(page=3, size=2)
    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1
    # Sem sobreposição entre páginas
    urls_p1 = {d.document_url for d in page1}
    urls_p2 = {d.document_url for d in page2}
    assert urls_p1.isdisjoint(urls_p2)


# ─── get_chunks ───────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_chunks_retorna_lista_vazia_sem_chunks(
    repo: PostgresDocumentRepository,
) -> None:
    chunks = await repo.get_chunks(DOC_URL)
    assert chunks == []


@pytest.mark.integration
async def test_get_chunks_retorna_chunks_em_ordem(pool, repo: PostgresDocumentRepository) -> None:
    async with pool.acquire() as conn:
        await _insert_content(conn)
        for i in range(3):
            await conn.execute(
                """
                INSERT INTO document_chunks
                    (document_url, chunk_index, chunk_text, token_count)
                VALUES ($1, $2, $3, $4)
                """,
                DOC_URL,
                i,
                f"Texto do chunk {i}",
                50,
            )
    chunks = await repo.get_chunks(DOC_URL)
    assert len(chunks) == 3
    assert all(isinstance(c, DocumentChunk) for c in chunks)
    assert [c.chunk_index for c in chunks] == [0, 1, 2]
    assert chunks[0].text == "Texto do chunk 0"
    assert chunks[0].token_count == 50


# ─── get_tables ───────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_tables_retorna_lista_vazia_sem_tabelas(
    repo: PostgresDocumentRepository,
) -> None:
    tables = await repo.get_tables(DOC_URL)
    assert tables == []


@pytest.mark.integration
async def test_get_tables_retorna_tabelas_em_ordem(pool, repo: PostgresDocumentRepository) -> None:
    async with pool.acquire() as conn:
        await _insert_content(conn)
        for i in range(2):
            await conn.execute(
                """
                INSERT INTO document_tables
                    (document_url, table_index, headers, rows, caption, num_rows, num_cols)
                VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7)
                """,
                DOC_URL,
                i,
                '["Col A", "Col B"]',
                '[["v1", "v2"]]',
                f"Tabela {i}",
                1,
                2,
            )
    tables = await repo.get_tables(DOC_URL)
    assert len(tables) == 2
    assert all(isinstance(t, DocumentTable) for t in tables)
    assert [t.table_index for t in tables] == [0, 1]
    assert tables[0].headers == ["Col A", "Col B"]
    assert tables[0].rows == [["v1", "v2"]]
    assert tables[0].num_rows == 1
    assert tables[0].num_cols == 2


# ─── save_content ─────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_save_content_cria_document_contents(pool, repo: PostgresDocumentRepository) -> None:
    """save_content deve upsert em document_contents e marcar como 'done'."""
    content = ProcessedDocument(
        document_url=DOC_URL,
        text="Texto completo do relatório.",
        num_pages=5,
        title="Relatório Salvo",
    )
    await repo.save_content(DOC_URL, content)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM document_contents WHERE document_url = $1", DOC_URL
        )
    assert row is not None
    assert row["processing_status"] == "done"
    assert row["full_text"] == "Texto completo do relatório."
    assert row["num_pages"] == 5
    assert row["document_title"] == "Relatório Salvo"


@pytest.mark.integration
async def test_save_content_substitui_chunks(pool, repo: PostgresDocumentRepository) -> None:
    """save_content deve deletar chunks antigos e inserir os novos."""
    # Primeiro save: 1 chunk
    content_v1 = ProcessedDocument(
        document_url=DOC_URL,
        text="Texto v1.",
        chunks=[
            DocumentChunk(id=0, document_url=DOC_URL, chunk_index=0, text="chunk v1", token_count=10)
        ],
    )
    await repo.save_content(DOC_URL, content_v1)
    # Segundo save: 2 chunks novos
    content_v2 = ProcessedDocument(
        document_url=DOC_URL,
        text="Texto v2.",
        chunks=[
            DocumentChunk(id=0, document_url=DOC_URL, chunk_index=0, text="chunk novo A", token_count=20),
            DocumentChunk(id=0, document_url=DOC_URL, chunk_index=1, text="chunk novo B", token_count=25),
        ],
    )
    await repo.save_content(DOC_URL, content_v2)
    chunks = await repo.get_chunks(DOC_URL)
    assert len(chunks) == 2
    assert chunks[0].text == "chunk novo A"
    assert chunks[1].text == "chunk novo B"


@pytest.mark.integration
async def test_save_content_substitui_tabelas(pool, repo: PostgresDocumentRepository) -> None:
    """save_content deve deletar tabelas antigas e inserir as novas."""
    content_v1 = ProcessedDocument(
        document_url=DOC_URL,
        text="Texto.",
        tables=[
            DocumentTable(
                id=0,
                document_url=DOC_URL,
                table_index=0,
                headers=["A"],
                rows=[["1"]],
                caption="Tabela antiga",
            )
        ],
    )
    await repo.save_content(DOC_URL, content_v1)
    content_v2 = ProcessedDocument(
        document_url=DOC_URL,
        text="Texto.",
        tables=[
            DocumentTable(
                id=0,
                document_url=DOC_URL,
                table_index=0,
                headers=["X", "Y"],
                rows=[["a", "b"], ["c", "d"]],
                caption="Tabela nova",
                num_rows=2,
                num_cols=2,
            )
        ],
    )
    await repo.save_content(DOC_URL, content_v2)
    tables = await repo.get_tables(DOC_URL)
    assert len(tables) == 1
    assert tables[0].caption == "Tabela nova"
    assert tables[0].headers == ["X", "Y"]
    assert len(tables[0].rows) == 2
