"""Testes E2E — Chat com documentos processados no banco (Etapa 9 Pipeline V2).

Critérios de aceite:
- ChatService.process_message() com dados de documentos no banco:
  - LLM recebe contexto com chunks relevantes
  - LLM recebe contexto com tabelas relevantes
  - search_tables('estagiários') encontra tabela de estagiários no banco
  - Resposta com tabelas estruturadas (quando LLM retorna)
  - Citações de documentos aparecem no ChatMessage (quando LLM retorna sources)
"""

from __future__ import annotations

import json

import asyncpg
import pytest

from app.adapters.outbound.postgres.inconsistency_repo import PostgresInconsistencyRepository
from app.adapters.outbound.postgres.search_repo import PostgresSearchRepository
from app.adapters.outbound.postgres.session_repo import PostgresSessionRepository
from app.domain.services.chat_service import ChatService
from tests.conftest import FakeAnalyticsRepository, FakeSessionRepository
from tests.e2e.conftest import SpyLLMGateway, docker_required

PAGE_URL = "https://www.tre-pi.jus.br/transparencia/pessoal"
CSV_URL = "https://www.tre-pi.jus.br/docs/estagiarios-2025.csv"
PDF_URL = "https://www.tre-pi.jus.br/docs/resolucao-123.pdf"


# ─── Helpers de seed ──────────────────────────────────────────────────────────


async def _seed_page(conn: asyncpg.Connection, url: str = PAGE_URL) -> None:
    await conn.execute(
        "INSERT INTO pages (url, title, category) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
        url, "Gestão de Pessoas", "Gestão de Pessoas",
    )


async def _seed_document(
    conn: asyncpg.Connection,
    doc_url: str,
    title: str,
    page_url: str = PAGE_URL,
    doc_type: str = "pdf",
) -> None:
    await conn.execute(
        """
        INSERT INTO documents (page_url, document_url, document_title, document_type, processing_status)
        VALUES ($1, $2, $3, $4, 'done')
        ON CONFLICT DO NOTHING
        """,
        page_url, doc_url, title, doc_type,
    )


async def _seed_document_content(conn: asyncpg.Connection, doc_url: str, title: str, text: str) -> None:
    await conn.execute(
        """
        INSERT INTO document_contents (document_url, document_title, full_text, num_pages, processing_status)
        VALUES ($1, $2, $3, 1, 'done')
        ON CONFLICT DO NOTHING
        """,
        doc_url, title, text,
    )


async def _seed_chunk(conn: asyncpg.Connection, doc_url: str, chunk_index: int, text: str) -> None:
    await conn.execute(
        """
        INSERT INTO document_chunks (document_url, chunk_index, chunk_text, token_count)
        VALUES ($1, $2, $3, $4)
        """,
        doc_url, chunk_index, text, len(text.split()),
    )


async def _seed_document_content_if_missing(
    conn: asyncpg.Connection, doc_url: str, title: str = "Doc"
) -> None:
    """Garante que document_contents existe (FK exigida por document_tables)."""
    await conn.execute(
        """
        INSERT INTO document_contents (document_url, document_title, full_text, num_pages, processing_status)
        VALUES ($1, $2, '', 1, 'done')
        ON CONFLICT DO NOTHING
        """,
        doc_url, title,
    )


async def _seed_table(
    conn: asyncpg.Connection,
    doc_url: str,
    caption: str,
    headers: list[str],
    rows: list[list[str]],
) -> None:
    # document_tables tem FK → document_contents
    await _seed_document_content_if_missing(conn, doc_url, caption)
    search_text = " ".join(headers) + " " + caption + " " + " ".join(
        cell for row in rows for cell in row
    )
    await conn.execute(
        """
        INSERT INTO document_tables
            (document_url, table_index, headers, rows, caption, num_rows, num_cols, search_text)
        VALUES ($1, 0, $2::jsonb, $3::jsonb, $4, $5, $6, $7)
        """,
        doc_url,
        json.dumps(headers, ensure_ascii=False),
        json.dumps(rows, ensure_ascii=False),
        caption,
        len(rows),
        len(headers),
        search_text,
    )


def _make_chat_service(pool: asyncpg.Pool, llm: SpyLLMGateway | None = None) -> ChatService:
    return ChatService(
        search_repo=PostgresSearchRepository(pool),
        session_repo=FakeSessionRepository(),
        analytics_repo=FakeAnalyticsRepository(),
        llm=llm or SpyLLMGateway(),
    )


# ─── Testes de busca de tabelas ───────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_search_tables_encontra_tabela_de_estagiarios(
    pool_e2e: asyncpg.Pool,
) -> None:
    """search_tables('estagiários') deve retornar tabela de estagiários do banco."""
    async with pool_e2e.acquire() as conn:
        await _seed_page(conn)
        await _seed_document(conn, CSV_URL, "Estagiários 2025", doc_type="csv")
        await _seed_table(
            conn, CSV_URL,
            caption="Lista de Estagiários TRE-PI 2025",
            headers=["Nome", "Matrícula", "Setor", "Período"],
            rows=[
                ["Maria Souza", "E001", "TI", "2025/1"],
                ["Pedro Lima", "E002", "Atendimento", "2025/1"],
            ],
        )

    search_repo = PostgresSearchRepository(pool_e2e)
    results = await search_repo.search_tables("estagiários")

    assert len(results) >= 1
    captions = [t.caption for t in results]
    assert any("Estagiários" in (c or "") for c in captions)


@docker_required
@pytest.mark.integration
async def test_search_tables_retorna_cabecalhos_e_linhas(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Tabela retornada deve ter headers e rows corretamente populados."""
    async with pool_e2e.acquire() as conn:
        await _seed_page(conn)
        await _seed_document(conn, CSV_URL, "Dados CSV", doc_type="csv")
        await _seed_table(
            conn, CSV_URL,
            caption="Contratos 2025",
            headers=["Fornecedor", "Valor", "Vigência"],
            rows=[["Empresa X", "R$ 100.000", "2025-12-31"]],
        )

    search_repo = PostgresSearchRepository(pool_e2e)
    results = await search_repo.search_tables("contratos")

    assert len(results) >= 1
    tbl = results[0]
    assert tbl.headers == ["Fornecedor", "Valor", "Vigência"]
    assert len(tbl.rows) == 1
    assert tbl.rows[0] == ["Empresa X", "R$ 100.000", "2025-12-31"]


# ─── Testes do ChatService com contexto de documentos ─────────────────────────


@docker_required
@pytest.mark.integration
async def test_process_message_envia_contexto_de_tabela_ao_llm(
    pool_e2e: asyncpg.Pool,
) -> None:
    """ChatService deve incluir tabela no contexto enviado ao LLM."""
    async with pool_e2e.acquire() as conn:
        await _seed_page(conn)
        await _seed_document(conn, CSV_URL, "Estagiários 2025", doc_type="csv")
        await _seed_table(
            conn, CSV_URL,
            caption="Estagiários TRE-PI",
            headers=["Nome", "Setor"],
            rows=[["Ana Costa", "Informática"]],
        )

    spy_llm = SpyLLMGateway()
    svc = _make_chat_service(pool_e2e, llm=spy_llm)

    await svc.process_message("Quais são os estagiários do TRE-PI?")

    assert spy_llm.call_count == 1
    # O contexto passado ao LLM deve conter referência à tabela de estagiários
    last_user_content = spy_llm.last_messages[-1]["content"]
    assert "Estagiários" in last_user_content or "estagiário" in last_user_content.lower()


@docker_required
@pytest.mark.integration
async def test_process_message_retorna_tabelas_quando_llm_inclui_na_resposta(
    pool_e2e: asyncpg.Pool,
) -> None:
    """ChatMessage.tables deve ser populado quando LLM retorna tabelas no JSON."""
    llm = SpyLLMGateway(response={
        "text": "Os estagiários do TRE-PI em 2025 são os listados abaixo.",
        "sources": [],
        "tables": [
            {
                "title": "Estagiários TRE-PI",
                "source_document": "estagiarios-2025.csv",
                "headers": ["Nome", "Setor"],
                "rows": [["Ana Costa", "Informática"]],
                "page_number": 1,
            }
        ],
        "suggestions": ["Ver detalhes"],
    })

    async with pool_e2e.acquire() as conn:
        await _seed_page(conn)

    svc = _make_chat_service(pool_e2e, llm=llm)
    msg = await svc.process_message("Quais são os estagiários do TRE-PI?")

    assert msg.content == "Os estagiários do TRE-PI em 2025 são os listados abaixo."
    assert len(msg.tables) == 1
    assert msg.tables[0].title == "Estagiários TRE-PI"
    assert msg.tables[0].headers == ["Nome", "Setor"]


@docker_required
@pytest.mark.integration
async def test_process_message_retorna_citacoes_quando_llm_inclui_sources(
    pool_e2e: asyncpg.Pool,
) -> None:
    """ChatMessage.sources deve conter citações quando LLM retorna sources."""
    llm = SpyLLMGateway(response={
        "text": "Conforme a Resolução 123/2025:",
        "sources": [
            {
                "document_title": "Resolução 123/2025",
                "document_url": PDF_URL,
                "snippet": "Art. 1º — O TRE-PI institui...",
                "page_number": 1,
            }
        ],
        "tables": [],
        "suggestions": [],
    })

    async with pool_e2e.acquire() as conn:
        await _seed_page(conn)
        await _seed_document(conn, PDF_URL, "Resolução 123/2025")
        await _seed_document_content(conn, PDF_URL, "Resolução 123/2025", "Art. 1º texto")

    svc = _make_chat_service(pool_e2e, llm=llm)
    msg = await svc.process_message("O que diz a resolução 123?")

    assert len(msg.sources) == 1
    assert msg.sources[0].document_title == "Resolução 123/2025"
    assert msg.sources[0].snippet == "Art. 1º — O TRE-PI institui..."
    assert msg.sources[0].page_number == 1


@docker_required
@pytest.mark.integration
async def test_process_message_registra_analytics(
    pool_e2e: asyncpg.Pool,
) -> None:
    """ChatService deve registrar a consulta nas analytics."""
    fake_analytics = FakeAnalyticsRepository()
    svc = ChatService(
        search_repo=PostgresSearchRepository(pool_e2e),
        session_repo=FakeSessionRepository(),
        analytics_repo=fake_analytics,
        llm=SpyLLMGateway(),
    )

    async with pool_e2e.acquire() as conn:
        await _seed_page(conn)

    await svc.process_message("Quais contratos estão vigentes?")

    assert len(fake_analytics._queries) == 1
    assert fake_analytics._queries[0]["query"] == "Quais contratos estão vigentes?"
