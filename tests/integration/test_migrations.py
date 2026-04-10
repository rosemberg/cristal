"""Testes de integração para Alembic migrations — Etapa 2.

TDD: estes testes definem o comportamento esperado das migrations ANTES
da implementação (RED). Após criar alembic.ini + env.py + 001_initial_schema.py,
todos devem passar (GREEN).

Critérios de saída (Etapa 2):
- alembic upgrade head cria todas as tabelas.
- alembic downgrade base remove todas as tabelas.
- Trigger de search_vector funciona (pages + document_chunks).
- Views transparency_stats e transparency_map existem.
- Extensão pg_trgm está instalada.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from tests.integration.conftest import docker_required

# ─── Constantes ──────────────────────────────────────────────────────────────

EXPECTED_TABLES = [
    "pages",
    "documents",
    "page_links",
    "navigation_tree",
    "document_contents",
    "document_chunks",
    "document_tables",
    "chat_sessions",
    "chat_messages",
    "query_logs",
]

EXPECTED_VIEWS = [
    "transparency_stats",
    "transparency_map",
]

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_alembic_config(database_url: str) -> Config:
    """Cria Config do Alembic apontando para o banco de teste."""
    ini_path = PROJECT_ROOT / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", database_url)
    # Garante que env.py encontre o pacote app/
    cfg.set_main_option("prepend_sys_path", str(PROJECT_ROOT))
    return cfg


def _asyncpg_dsn(database_url: str) -> str:
    """Converte URL SQLAlchemy asyncpg para DSN nativo asyncpg."""
    return (
        database_url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
    )


async def _list_tables(dsn: str) -> list[str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            ORDER BY table_name
            """
        )
        return [r["table_name"] for r in rows]
    finally:
        await conn.close()


async def _list_views(dsn: str) -> list[str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.views
            WHERE table_schema = 'public'
            ORDER BY table_name
            """
        )
        return [r["table_name"] for r in rows]
    finally:
        await conn.close()


async def _extension_exists(dsn: str, extname: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        result = await conn.fetchval(
            "SELECT COUNT(*) FROM pg_extension WHERE extname = $1", extname
        )
        return bool(result)
    finally:
        await conn.close()


# ─── Fixture de ciclo de vida das migrations ─────────────────────────────────


@pytest.fixture(scope="module")
def migrated_db(pg_settings):  # type: ignore[no-untyped-def]
    """Roda alembic upgrade head uma vez para o módulo e faz downgrade no teardown."""
    dsn_url = pg_settings.database_url
    cfg = make_alembic_config(dsn_url)

    # Garante estado limpo antes
    command.downgrade(cfg, "base")

    # Aplica todas as migrations
    command.upgrade(cfg, "head")

    yield cfg, dsn_url

    # Teardown: remove tudo
    command.downgrade(cfg, "base")


# ─── Testes ───────────────────────────────────────────────────────────────────


@docker_required
def test_upgrade_cria_todas_as_tabelas(migrated_db):  # type: ignore[no-untyped-def]
    """Após upgrade head, todas as tabelas do schema devem existir."""
    _cfg, dsn_url = migrated_db
    dsn = _asyncpg_dsn(dsn_url)
    tables = asyncio.run(_list_tables(dsn))

    for table in EXPECTED_TABLES:
        assert table in tables, f"Tabela '{table}' não encontrada após upgrade head"


@docker_required
def test_upgrade_cria_views(migrated_db):  # type: ignore[no-untyped-def]
    """Após upgrade head, as views de conveniência devem existir."""
    _cfg, dsn_url = migrated_db
    dsn = _asyncpg_dsn(dsn_url)
    views = asyncio.run(_list_views(dsn))

    for view in EXPECTED_VIEWS:
        assert view in views, f"View '{view}' não encontrada após upgrade head"


@docker_required
def test_extensao_pg_trgm_instalada(migrated_db):  # type: ignore[no-untyped-def]
    """A extensão pg_trgm deve estar instalada para busca fuzzy."""
    _cfg, dsn_url = migrated_db
    dsn = _asyncpg_dsn(dsn_url)
    exists = asyncio.run(_extension_exists(dsn, "pg_trgm"))
    assert exists, "Extensão pg_trgm não encontrada"


@docker_required
def test_trigger_pages_search_vector(migrated_db):  # type: ignore[no-untyped-def]
    """INSERT em pages deve preencher search_vector automaticamente via trigger."""

    async def _check(dsn: str) -> bool:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                """
                INSERT INTO pages (url, title, description, category)
                VALUES ($1, $2, $3, $4)
                """,
                "https://www.tre-pi.jus.br/transparencia/test",
                "Transparência — Teste",
                "Página de teste do portal de transparência",
                "Transparência",
            )
            result = await conn.fetchval(
                "SELECT search_vector IS NOT NULL FROM pages WHERE url = $1",
                "https://www.tre-pi.jus.br/transparencia/test",
            )
            return bool(result)
        finally:
            await conn.execute(
                "DELETE FROM pages WHERE url = $1",
                "https://www.tre-pi.jus.br/transparencia/test",
            )
            await conn.close()

    _cfg, dsn_url = migrated_db
    dsn = _asyncpg_dsn(dsn_url)
    populated = asyncio.run(_check(dsn))
    assert populated, "Trigger pages_search_update não preencheu search_vector"


@docker_required
def test_trigger_document_chunks_search_vector(migrated_db):  # type: ignore[no-untyped-def]
    """INSERT em document_chunks deve preencher search_vector via trigger."""

    async def _check(dsn: str) -> bool:
        conn = await asyncpg.connect(dsn)
        try:
            # document_chunks requer document_contents
            await conn.execute(
                "INSERT INTO document_contents (document_url, document_title, document_type)"
                " VALUES ($1, $2, $3)",
                "https://www.tre-pi.jus.br/doc/test.pdf",
                "Documento de Teste",
                "pdf",
            )
            await conn.execute(
                """
                INSERT INTO document_chunks (document_url, chunk_index, chunk_text, section_title)
                VALUES ($1, $2, $3, $4)
                """,
                "https://www.tre-pi.jus.br/doc/test.pdf",
                0,
                "Conteúdo do chunk de teste para verificar o trigger de busca.",
                "Seção de Teste",
            )
            result = await conn.fetchval(
                "SELECT search_vector IS NOT NULL FROM document_chunks"
                " WHERE document_url = $1 AND chunk_index = 0",
                "https://www.tre-pi.jus.br/doc/test.pdf",
            )
            return bool(result)
        finally:
            await conn.execute(
                "DELETE FROM document_contents WHERE document_url = $1",
                "https://www.tre-pi.jus.br/doc/test.pdf",
            )
            await conn.close()

    _cfg, dsn_url = migrated_db
    dsn = _asyncpg_dsn(dsn_url)
    populated = asyncio.run(_check(dsn))
    assert populated, "Trigger chunks_search_update não preencheu search_vector"


@docker_required
def test_chat_sessions_usa_uuid(migrated_db):  # type: ignore[no-untyped-def]
    """chat_sessions.id deve ser UUID gerado automaticamente."""

    async def _check(dsn: str) -> str:
        conn = await asyncpg.connect(dsn)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO chat_sessions (title) VALUES ($1) RETURNING id::text",
                "Sessão de Teste",
            )
            await conn.execute("DELETE FROM chat_sessions WHERE title = $1", "Sessão de Teste")
            return str(session_id)
        finally:
            await conn.close()

    _cfg, dsn_url = migrated_db
    dsn = _asyncpg_dsn(dsn_url)
    session_id = asyncio.run(_check(dsn))
    # UUID tem 36 caracteres no formato xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    assert len(session_id) == 36, f"ID da sessão não parece UUID: {session_id!r}"
    assert session_id.count("-") == 4, f"ID da sessão não parece UUID: {session_id!r}"


@docker_required
def test_indice_date_query_logs(migrated_db):  # type: ignore[no-untyped-def]
    """Índice idx_logs_created_date deve existir para analytics temporal."""

    async def _check(dsn: str) -> bool:
        conn = await asyncpg.connect(dsn)
        try:
            result = await conn.fetchval(
                """
                SELECT COUNT(*) FROM pg_indexes
                WHERE tablename = 'query_logs'
                  AND indexname = 'idx_logs_created_date'
                """
            )
            return bool(result)
        finally:
            await conn.close()

    _cfg, dsn_url = migrated_db
    dsn = _asyncpg_dsn(dsn_url)
    exists = asyncio.run(_check(dsn))
    assert exists, "Índice idx_logs_created_date não encontrado em query_logs"


@docker_required
def test_downgrade_remove_todas_as_tabelas(pg_settings):  # type: ignore[no-untyped-def]
    """Após downgrade base, nenhuma tabela do schema deve existir.

    ATENÇÃO: este teste deve rodar APÓS o fixture migrated_db ter feito
    seu teardown (downgrade). Usamos pg_settings diretamente, não migrated_db.
    """
    dsn_url = pg_settings.database_url
    cfg = make_alembic_config(dsn_url)

    # Garante estado: faz upgrade para ter algo, depois downgrade
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    dsn = _asyncpg_dsn(dsn_url)
    tables = asyncio.run(_list_tables(dsn))

    for table in EXPECTED_TABLES:
        assert table not in tables, f"Tabela '{table}' ainda existe após downgrade base"


@docker_required
def test_upgrade_idempotente(pg_settings):  # type: ignore[no-untyped-def]
    """Aplicar upgrade head duas vezes não deve causar erro (idempotência via Alembic)."""
    dsn_url = pg_settings.database_url
    cfg = make_alembic_config(dsn_url)

    command.upgrade(cfg, "head")
    # Segunda aplicação — Alembic detecta que já está na head e não faz nada
    command.upgrade(cfg, "head")

    dsn = _asyncpg_dsn(dsn_url)
    tables = asyncio.run(_list_tables(dsn))
    assert "pages" in tables, "Tabela 'pages' não encontrada após double-upgrade"

    # Cleanup final
    command.downgrade(cfg, "base")
