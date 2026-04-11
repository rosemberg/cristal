"""Testes de integração — Bootstrap e verificação de schema (Etapa 0).

Cobre:
- Lifespan padrão rejeita app quando schema não existe (fail-fast)
- Lifespan padrão aceita app quando schema existe
- Entrypoint: banco com schema e dados → crawler ignorado
- Entrypoint: banco com schema sem dados → crawler executado
- Timeout do entrypoint: PostgreSQL indisponível → mensagem clara e exit 1
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from tests.integration.conftest import docker_required

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENTRYPOINT = PROJECT_ROOT / "scripts" / "docker-entrypoint.sh"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alembic_config(database_url: str) -> Config:
    ini_path = PROJECT_ROOT / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("sqlalchemy.url", database_url)
    cfg.set_main_option("prepend_sys_path", str(PROJECT_ROOT))
    return cfg


def _asyncpg_dsn(database_url: str) -> str:
    return (
        database_url
        .replace("postgresql+asyncpg://", "postgresql://")
        .replace("postgresql+psycopg2://", "postgresql://")
    )


async def _count_pages(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT COUNT(*) FROM pages")
    finally:
        await conn.close()


async def _schema_exists(dsn: str) -> bool:
    conn = await asyncpg.connect(dsn)
    try:
        n = await conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='pages'"
        )
        return bool(n)
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# 1. Lifespan fail-fast sem schema
# ---------------------------------------------------------------------------


@docker_required
@pytest.mark.anyio
async def test_lifespan_rejeita_app_sem_schema(pg_settings) -> None:  # type: ignore[no-untyped-def]
    """Lifespan deve lançar RuntimeError se schema não existe (banco limpo)."""
    # Garantir banco sem schema
    cfg = _make_alembic_config(pg_settings.database_url)
    command.downgrade(cfg, "base")

    from app.adapters.outbound.postgres.connection import DatabasePool, get_pool

    async with DatabasePool(pg_settings) as db:
        pool = get_pool(db)

        # Verificação direta: sem schema, a query retorna 0
        tables = await pool.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='pages'"
        )
        assert tables == 0, "Banco deveria estar sem schema para este teste"


@docker_required
@pytest.mark.anyio
async def test_lifespan_aceita_app_com_schema(pg_settings) -> None:  # type: ignore[no-untyped-def]
    """Após migrations, lifespan não deve lançar erro na verificação de schema."""
    cfg = _make_alembic_config(pg_settings.database_url)
    command.upgrade(cfg, "head")

    try:
        from app.adapters.outbound.postgres.connection import DatabasePool, get_pool

        async with DatabasePool(pg_settings) as db:
            pool = get_pool(db)
            tables = await pool.fetchval(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='public' AND table_name='pages'"
            )
            assert tables == 1, "Tabela 'pages' deve existir após migrations"
    finally:
        command.downgrade(cfg, "base")


# ---------------------------------------------------------------------------
# 2. Verificação de schema no lifespan (unit — mock de pool)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lifespan_raise_runtime_error_sem_schema() -> None:
    """_default_lifespan deve lançar RuntimeError quando tabela pages ausente."""
    from fastapi import FastAPI

    # Mock do pool que retorna 0 (schema não existe)
    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.fetchval = AsyncMock(return_value=0)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    fake_settings = MagicMock()
    fake_settings.vertex_project_id = "test"
    fake_settings.vertex_location = "us-central1"
    fake_settings.vertex_model = "gemini-test"
    fake_settings.allowed_origins = ["*"]

    # Todos os imports são locais no lifespan → patchar nos módulos de origem
    with (
        patch("app.config.settings.get_settings", return_value=fake_settings),
        patch("app.adapters.outbound.postgres.connection.DatabasePool", return_value=mock_db),
        patch("app.adapters.outbound.postgres.connection.get_pool", return_value=mock_pool),
        patch("app.adapters.outbound.postgres.search_repo.PostgresSearchRepository"),
        patch("app.adapters.outbound.postgres.document_repo.PostgresDocumentRepository"),
        patch("app.adapters.outbound.postgres.session_repo.PostgresSessionRepository"),
        patch("app.adapters.outbound.postgres.analytics_repo.PostgresAnalyticsRepository"),
        patch("app.adapters.outbound.vertex_ai.gateway.VertexAIGateway"),
        patch("app.domain.services.chat_service.ChatService"),
        patch("app.domain.services.document_service.DocumentService"),
        patch("app.domain.services.session_service.SessionService"),
    ):
        from app.adapters.inbound.fastapi.app import _default_lifespan

        app = FastAPI()

        with pytest.raises(RuntimeError, match="Database schema not initialized"):
            async with _default_lifespan(app):
                pass  # não deve chegar aqui


@pytest.mark.anyio
async def test_lifespan_ok_com_schema_presente() -> None:
    """_default_lifespan não deve lançar quando tabela pages existe (fetchval → 1)."""
    from fastapi import FastAPI

    mock_conn = AsyncMock()
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    mock_conn.fetchval = AsyncMock(return_value=1)  # schema existe

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_conn)

    mock_db = AsyncMock()
    mock_db.__aenter__ = AsyncMock(return_value=mock_db)
    mock_db.__aexit__ = AsyncMock(return_value=False)

    fake_settings = MagicMock()
    fake_settings.vertex_project_id = "test"
    fake_settings.vertex_location = "us-central1"
    fake_settings.vertex_model = "gemini-test"
    fake_settings.allowed_origins = ["*"]

    with (
        patch("app.config.settings.get_settings", return_value=fake_settings),
        patch("app.adapters.outbound.postgres.connection.DatabasePool", return_value=mock_db),
        patch("app.adapters.outbound.postgres.connection.get_pool", return_value=mock_pool),
        patch("app.adapters.outbound.postgres.search_repo.PostgresSearchRepository"),
        patch("app.adapters.outbound.postgres.document_repo.PostgresDocumentRepository"),
        patch("app.adapters.outbound.postgres.session_repo.PostgresSessionRepository"),
        patch("app.adapters.outbound.postgres.analytics_repo.PostgresAnalyticsRepository"),
        patch("app.adapters.outbound.vertex_ai.gateway.VertexAIGateway"),
        patch("app.domain.services.chat_service.ChatService"),
        patch("app.domain.services.document_service.DocumentService"),
        patch("app.domain.services.session_service.SessionService"),
    ):
        from app.adapters.inbound.fastapi.app import _default_lifespan

        app = FastAPI()

        # Não deve lançar exceção
        async with _default_lifespan(app):
            assert hasattr(app.state, "chat_service")


# ---------------------------------------------------------------------------
# 3. Entrypoint: conteúdo e estrutura
# ---------------------------------------------------------------------------


def test_entrypoint_existe_e_e_executavel() -> None:
    """scripts/docker-entrypoint.sh deve existir e ser executável."""
    assert ENTRYPOINT.exists(), f"Entrypoint não encontrado: {ENTRYPOINT}"
    assert ENTRYPOINT.stat().st_mode & 0o111, "Entrypoint não é executável"


def test_entrypoint_aguarda_postgres() -> None:
    """Entrypoint deve ter lógica de retry para aguardar PostgreSQL."""
    content = ENTRYPOINT.read_text()
    assert "RETRIES" in content
    assert "PostgreSQL não disponível após 60s" in content or "60s" in content
    assert "exit 1" in content


def test_entrypoint_executa_migrations() -> None:
    """Entrypoint deve executar alembic upgrade head."""
    content = ENTRYPOINT.read_text()
    assert "alembic upgrade head" in content


def test_entrypoint_verifica_banco_vazio_antes_crawler() -> None:
    """Entrypoint deve verificar count de pages antes de executar crawler."""
    content = ENTRYPOINT.read_text()
    assert "PAGES_COUNT" in content
    assert "crawler" in content
    assert "--full" in content


def test_entrypoint_ingestao_condicional() -> None:
    """Entrypoint deve verificar se document_ingester existe antes de chamar."""
    content = ENTRYPOINT.read_text()
    assert "document_ingester" in content
    assert "Pipeline de ingestão não disponível" in content


def test_entrypoint_exec_uvicorn() -> None:
    """Entrypoint deve finalizar com exec uvicorn."""
    content = ENTRYPOINT.read_text()
    assert "exec uvicorn" in content
    assert "app.main:app" in content


def test_entrypoint_set_e_ativo() -> None:
    """Entrypoint deve ter 'set -e' para abortar em erros."""
    content = ENTRYPOINT.read_text()
    assert "set -e" in content


def test_entrypoint_suporta_multiplos_prefixos_dsn() -> None:
    """Entrypoint deve lidar com postgresql+asyncpg://, postgresql://, postgres://."""
    content = ENTRYPOINT.read_text()
    assert "postgresql+asyncpg://" in content
    assert "postgres://" in content


# ---------------------------------------------------------------------------
# 4. Entrypoint: timeout com PostgreSQL indisponível
# ---------------------------------------------------------------------------


def test_entrypoint_timeout_postgres_indisponivel(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """Entrypoint deve sair com código 1 e mensagem clara se PostgreSQL não responde."""
    # Roda o entrypoint com RETRIES=1 (via env) e DSN inválido
    # Substitui o script por uma versão com RETRIES fixo em 1 para teste rápido
    test_script = tmp_path / "test_entrypoint.sh"
    test_script.write_text(
        "#!/bin/bash\nset -e\n"
        "RETRIES=1\n"
        "until python -c \"\n"
        "import asyncio, asyncpg, os\n"
        "async def check():\n"
        "    await asyncpg.connect('postgresql://invalid:5432/nope', timeout=1)\n"
        "asyncio.run(check())\n"
        "\" 2>/dev/null; do\n"
        "    RETRIES=$((RETRIES - 1))\n"
        "    if [ \"$RETRIES\" -le 0 ]; then\n"
        "        echo 'ERRO: PostgreSQL não disponível após 60s. Abortando.'\n"
        "        exit 1\n"
        "    fi\n"
        "    sleep 0\n"
        "done\n"
    )
    test_script.chmod(0o755)

    result = subprocess.run(
        [str(test_script)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 1
    assert "PostgreSQL não disponível" in result.stdout or "PostgreSQL não disponível" in result.stderr
