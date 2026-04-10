"""Alembic environment — runner assíncrono com asyncpg.

Suporta dois modos:
- online: conecta ao banco e aplica as migrations diretamente.
- offline: gera SQL sem conexão (útil para revisão manual ou CI).

A URL do banco é lida de app.config.settings (via CRISTAL_DATABASE_URL).
Para sobrescrever em testes ou CLI, passe sqlalchemy.url via Config.set_main_option().
"""

from __future__ import annotations

import asyncio
import logging
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger(__name__)

# Config object — acesso ao alembic.ini
config = context.config

# Configura logging a partir do alembic.ini (se disponível)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)


# ─── URL do banco ─────────────────────────────────────────────────────────────


def _get_database_url() -> str:
    """Retorna a URL do banco de dados.

    Prioridade:
    1. sqlalchemy.url definida via config (ex: testes com set_main_option)
    2. app.config.settings (lê CRISTAL_DATABASE_URL do ambiente)
    """
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from app.config.settings import get_settings

    return get_settings().database_url


# ─── Modo offline ────────────────────────────────────────────────────────────


def run_migrations_offline() -> None:
    """Gera SQL das migrations sem conectar ao banco.

    Útil para gerar scripts de revisão ou para ambientes sem acesso direto ao BD.
    """
    url = _get_database_url()
    context.configure(
        url=url,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


# ─── Modo online (async) ──────────────────────────────────────────────────────


def _do_run_migrations(connection: object) -> None:
    """Executa as migrations usando a conexão síncrona fornecida pelo run_sync."""
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


async def _run_migrations_online_async() -> None:
    """Cria engine assíncrona e aplica as migrations via run_sync."""
    url = _get_database_url()
    connectable = create_async_engine(
        url,
        future=True,
        # Desativa o pool durante migrations para evitar conexões ociosas
        poolclass=sa.pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    """Ponto de entrada para migrations em modo online.

    Se já existe um event loop rodando (ex: pytest-asyncio), executa em
    uma thread separada com seu próprio loop para evitar conflito.
    """
    import concurrent.futures

    def _run_in_new_loop() -> None:
        new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(new_loop)
        try:
            new_loop.run_until_complete(_run_migrations_online_async())
        finally:
            new_loop.close()

    try:
        asyncio.get_running_loop()
        # Já existe loop rodando — executa em thread com loop próprio
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(_run_in_new_loop).result()
    except RuntimeError:
        # Nenhum loop rodando — uso direto via asyncio.run()
        asyncio.run(_run_migrations_online_async())


# ─── Dispatch ────────────────────────────────────────────────────────────────

if context.is_offline_mode():
    logger.info("Rodando migrations em modo offline.")
    run_migrations_offline()
else:
    logger.info("Rodando migrations em modo online.")
    run_migrations_online()
