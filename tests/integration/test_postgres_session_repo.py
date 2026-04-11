"""Integration tests: PostgresSessionRepository — Etapa 5 (TDD RED → GREEN).

Requer Docker disponível (testcontainers sobe PostgreSQL automaticamente).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.adapters.outbound.postgres.connection import create_pool
from app.adapters.outbound.postgres.session_repo import PostgresSessionRepository
from app.config.settings import Settings
from app.domain.entities.session import ChatSession
from app.domain.value_objects.chat_message import ChatMessage, Citation, TableData

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
    return PostgresSessionRepository(pool)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_user_message(text: str = "Qual é o orçamento?") -> ChatMessage:
    return ChatMessage(role="user", content=text, sources=[], tables=[])


def _make_assistant_message(text: str = "O orçamento é X.") -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content=text,
        sources=[
            Citation(
                document_title="Relatório Anual",
                document_url="https://www.tre-pi.jus.br/relatorio.pdf",
                snippet="O orçamento aprovado foi de R$ 10M.",
                page_number=3,
            )
        ],
        tables=[
            TableData(
                headers=["Descrição", "Valor"],
                rows=[["Orçamento", "R$ 10M"]],
                source_document="https://www.tre-pi.jus.br/relatorio.pdf",
                title="Tabela Orçamento",
                page_number=5,
            )
        ],
    )


# ─── create ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_create_retorna_sessao_com_uuid(repo: PostgresSessionRepository) -> None:
    """create deve retornar ChatSession com UUID válido."""
    session = await repo.create()
    assert isinstance(session, ChatSession)
    assert isinstance(session.id, uuid.UUID)
    assert session.messages == []
    assert session.title is None


@pytest.mark.integration
async def test_create_com_titulo(repo: PostgresSessionRepository) -> None:
    """create com title deve persistir o título."""
    session = await repo.create(title="Consulta Orçamento 2026")
    assert session.title == "Consulta Orçamento 2026"


@pytest.mark.integration
async def test_create_cria_sessoes_com_uuids_distintos(repo: PostgresSessionRepository) -> None:
    """Duas chamadas a create devem gerar UUIDs distintos."""
    s1 = await repo.create()
    s2 = await repo.create()
    assert s1.id != s2.id


# ─── get ──────────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_retorna_none_para_id_desconhecido(repo: PostgresSessionRepository) -> None:
    """get com UUID inexistente deve retornar None."""
    result = await repo.get(uuid.uuid4())
    assert result is None


@pytest.mark.integration
async def test_get_retorna_sessao_existente(repo: PostgresSessionRepository) -> None:
    """get deve retornar a sessão pelo UUID."""
    created = await repo.create(title="Sessão de Teste")
    retrieved = await repo.get(created.id)
    assert retrieved is not None
    assert retrieved.id == created.id
    assert retrieved.title == "Sessão de Teste"
    assert retrieved.messages == []


@pytest.mark.integration
async def test_get_retorna_sessao_com_mensagens(repo: PostgresSessionRepository) -> None:
    """get deve incluir as mensagens salvas na sessão."""
    session = await repo.create()
    await repo.save_message(session.id, _make_user_message("Primeira pergunta"))
    await repo.save_message(session.id, _make_assistant_message("Primeira resposta"))
    retrieved = await repo.get(session.id)
    assert retrieved is not None
    assert len(retrieved.messages) == 2
    assert retrieved.messages[0].role == "user"
    assert retrieved.messages[0].content == "Primeira pergunta"
    assert retrieved.messages[1].role == "assistant"


# ─── save_message ─────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_save_message_persiste_mensagem(repo: PostgresSessionRepository) -> None:
    """save_message deve adicionar mensagem à sessão."""
    session = await repo.create()
    msg = _make_user_message("Qual é o prazo?")
    await repo.save_message(session.id, msg)
    retrieved = await repo.get(session.id)
    assert retrieved is not None
    assert len(retrieved.messages) == 1
    assert retrieved.messages[0].content == "Qual é o prazo?"
    assert retrieved.messages[0].role == "user"


@pytest.mark.integration
async def test_save_message_preserva_sources_e_tables(repo: PostgresSessionRepository) -> None:
    """save_message deve serializar/deserializar sources e tables via JSONB."""
    session = await repo.create()
    await repo.save_message(session.id, _make_assistant_message())
    retrieved = await repo.get(session.id)
    assert retrieved is not None
    msg = retrieved.messages[0]
    assert len(msg.sources) == 1
    assert msg.sources[0].document_title == "Relatório Anual"
    assert msg.sources[0].page_number == 3
    assert len(msg.tables) == 1
    assert msg.tables[0].headers == ["Descrição", "Valor"]
    assert msg.tables[0].rows == [["Orçamento", "R$ 10M"]]


@pytest.mark.integration
async def test_save_message_atualiza_last_active(pool, repo: PostgresSessionRepository) -> None:
    """save_message deve atualizar last_active e message_count na sessão."""
    session = await repo.create()
    initial_active = session.last_active
    await repo.save_message(session.id, _make_user_message())
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT last_active, message_count FROM chat_sessions WHERE id = $1",
            session.id,
        )
    assert row is not None
    assert row["message_count"] == 1
    # last_active deve ser >= ao criado_at original (NOW() pode ser igual em testes rápidos)
    assert row["last_active"] >= initial_active


# ─── list_sessions ────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_list_sessions_retorna_lista_vazia(repo: PostgresSessionRepository) -> None:
    sessions = await repo.list_sessions()
    assert sessions == []


@pytest.mark.integration
async def test_list_sessions_retorna_sessoes_criadas(repo: PostgresSessionRepository) -> None:
    await repo.create(title="Sessão A")
    await repo.create(title="Sessão B")
    sessions = await repo.list_sessions()
    assert len(sessions) == 2
    titles = {s.title for s in sessions}
    assert "Sessão A" in titles
    assert "Sessão B" in titles


@pytest.mark.integration
async def test_list_sessions_respeita_limit(repo: PostgresSessionRepository) -> None:
    for i in range(5):
        await repo.create(title=f"Sessão {i}")
    sessions = await repo.list_sessions(limit=3)
    assert len(sessions) == 3


@pytest.mark.integration
async def test_list_sessions_ordenada_por_last_active(
    pool, repo: PostgresSessionRepository
) -> None:
    """list_sessions deve retornar sessões ordenadas por last_active DESC."""
    s1 = await repo.create(title="Antiga")
    s2 = await repo.create(title="Recente")
    # Força s2 a ser mais recente enviando uma mensagem
    await repo.save_message(s2.id, _make_user_message())
    sessions = await repo.list_sessions()
    assert sessions[0].id == s2.id  # mais recente primeiro
    assert sessions[1].id == s1.id
