"""Integration tests: PostgresAnalyticsRepository — Etapa 5 (TDD RED → GREEN).

Requer Docker disponível (testcontainers sobe PostgreSQL automaticamente).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.adapters.outbound.postgres.analytics_repo import PostgresAnalyticsRepository
from app.adapters.outbound.postgres.connection import create_pool
from app.config.settings import Settings

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
    return PostgresAnalyticsRepository(pool)


# ─── log_query ────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_log_query_retorna_id_inteiro(repo: PostgresAnalyticsRepository) -> None:
    """log_query deve persistir o registro e retornar seu id."""
    qid = await repo.log_query(
        session_id=None,
        query="Qual é o orçamento?",
        intent_type="busca_geral",
        pages_found=2,
        chunks_found=3,
        tables_found=1,
        response_time_ms=150,
    )
    assert isinstance(qid, int)
    assert qid >= 1


@pytest.mark.integration
async def test_log_query_sem_session_id(repo: PostgresAnalyticsRepository) -> None:
    """log_query sem session_id deve funcionar (campo nullable)."""
    qid = await repo.log_query(
        session_id=None,
        query="Quem são os servidores?",
        intent_type="consulta_dados",
        pages_found=0,
        chunks_found=5,
        tables_found=2,
        response_time_ms=300,
    )
    assert qid >= 1


@pytest.mark.integration
async def test_log_query_ids_sequenciais(repo: PostgresAnalyticsRepository) -> None:
    """IDs retornados por log_query devem ser crescentes."""
    id1 = await repo.log_query(
        session_id=None,
        query="Pergunta 1",
        intent_type="busca_geral",
        pages_found=1,
        chunks_found=0,
        tables_found=0,
        response_time_ms=100,
    )
    id2 = await repo.log_query(
        session_id=None,
        query="Pergunta 2",
        intent_type="busca_geral",
        pages_found=2,
        chunks_found=0,
        tables_found=0,
        response_time_ms=200,
    )
    assert id2 > id1


@pytest.mark.integration
async def test_log_query_persiste_todos_os_campos(
    pool, repo: PostgresAnalyticsRepository
) -> None:
    """log_query deve salvar todos os campos corretamente."""
    qid = await repo.log_query(
        session_id=None,
        query="Consulta detalhada",
        intent_type="consulta_documento",
        pages_found=3,
        chunks_found=7,
        tables_found=2,
        response_time_ms=450,
    )
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM query_logs WHERE id = $1", qid)
    assert row is not None
    assert row["query"] == "Consulta detalhada"
    assert row["intent_type"] == "consulta_documento"
    assert row["pages_found"] == 3
    assert row["chunks_found"] == 7
    assert row["tables_found"] == 2
    assert row["response_time_ms"] == 450
    assert row["feedback"] is None


# ─── update_feedback ──────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_update_feedback_positive(pool, repo: PostgresAnalyticsRepository) -> None:
    """update_feedback deve salvar feedback 'positive' no registro."""
    qid = await repo.log_query(
        session_id=None,
        query="Pergunta",
        intent_type="busca_geral",
        pages_found=1,
        chunks_found=0,
        tables_found=0,
        response_time_ms=100,
    )
    await repo.update_feedback(qid, "positive")
    async with pool.acquire() as conn:
        feedback = await conn.fetchval(
            "SELECT feedback FROM query_logs WHERE id = $1", qid
        )
    assert feedback == "positive"


@pytest.mark.integration
async def test_update_feedback_negative(pool, repo: PostgresAnalyticsRepository) -> None:
    """update_feedback deve salvar feedback 'negative'."""
    qid = await repo.log_query(
        session_id=None,
        query="Outra pergunta",
        intent_type="navegacao",
        pages_found=0,
        chunks_found=0,
        tables_found=0,
        response_time_ms=50,
    )
    await repo.update_feedback(qid, "negative")
    async with pool.acquire() as conn:
        feedback = await conn.fetchval(
            "SELECT feedback FROM query_logs WHERE id = $1", qid
        )
    assert feedback == "negative"


# ─── get_metrics ──────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_metrics_retorna_zeros_sem_queries(repo: PostgresAnalyticsRepository) -> None:
    """get_metrics deve retornar contagens zeradas quando não há registros."""
    metrics = await repo.get_metrics(days=30)
    assert metrics["total_queries"] == 0
    assert float(metrics["avg_response_time_ms"]) == 0.0


@pytest.mark.integration
async def test_get_metrics_com_queries(repo: PostgresAnalyticsRepository) -> None:
    """get_metrics deve agregar corretamente as queries registradas."""
    await repo.log_query(
        session_id=None,
        query="Q1",
        intent_type="busca_geral",
        pages_found=1,
        chunks_found=0,
        tables_found=0,
        response_time_ms=100,
    )
    id2 = await repo.log_query(
        session_id=None,
        query="Q2",
        intent_type="busca_geral",
        pages_found=0,
        chunks_found=2,
        tables_found=0,
        response_time_ms=300,
    )
    await repo.update_feedback(id2, "positive")
    metrics = await repo.get_metrics(days=30)
    assert metrics["total_queries"] == 2
    assert float(metrics["avg_response_time_ms"]) == 200.0
    assert metrics["positive_feedback"] == 1
    assert metrics["negative_feedback"] == 0


# ─── get_daily_stats ──────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_daily_stats_retorna_lista_vazia_sem_queries(
    repo: PostgresAnalyticsRepository,
) -> None:
    """get_daily_stats deve retornar [] quando não há queries."""
    stats = await repo.get_daily_stats(days=30)
    assert stats == []


@pytest.mark.integration
async def test_get_daily_stats_com_queries(repo: PostgresAnalyticsRepository) -> None:
    """get_daily_stats deve retornar uma entrada por dia com os dados corretos."""
    await repo.log_query(
        session_id=None,
        query="Q1",
        intent_type="busca_geral",
        pages_found=1,
        chunks_found=0,
        tables_found=0,
        response_time_ms=200,
    )
    await repo.log_query(
        session_id=None,
        query="Q2",
        intent_type="navegacao",
        pages_found=0,
        chunks_found=0,
        tables_found=0,
        response_time_ms=400,
    )
    stats = await repo.get_daily_stats(days=30)
    assert len(stats) == 1  # ambas as queries são do mesmo dia
    day = stats[0]
    assert "date" in day
    assert day["query_count"] == 2
    assert float(day["avg_response_time_ms"]) == 300.0


@pytest.mark.integration
async def test_get_daily_stats_chaves_do_dict(repo: PostgresAnalyticsRepository) -> None:
    """Cada dict em get_daily_stats deve ter as chaves esperadas."""
    await repo.log_query(
        session_id=None,
        query="Teste",
        intent_type="busca_geral",
        pages_found=0,
        chunks_found=0,
        tables_found=0,
        response_time_ms=50,
    )
    stats = await repo.get_daily_stats(days=30)
    assert len(stats) >= 1
    entry = stats[0]
    assert "date" in entry
    assert "query_count" in entry
    assert "avg_response_time_ms" in entry
