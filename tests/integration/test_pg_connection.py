"""Integration tests for PostgreSQL connection — Etapa 1 (TDD RED → GREEN).

Requer Docker disponível (testcontainers sobe PostgreSQL automaticamente).
"""

import pytest

from app.adapters.outbound.postgres.connection import (
    DatabasePool,
    create_pool,
    get_pool,
)
from app.config.settings import Settings


@pytest.mark.integration
async def test_pool_creates_and_closes(pg_settings: Settings) -> None:
    """Pool deve criar conexões e fechar sem erro."""
    pool = await create_pool(pg_settings)
    assert pool is not None
    await pool.close()


@pytest.mark.integration
async def test_pool_can_acquire_connection(pg_settings: Settings) -> None:
    """Pool deve fornecer uma conexão funcional."""
    pool = await create_pool(pg_settings)
    try:
        async with pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
            assert result == 1
    finally:
        await pool.close()


@pytest.mark.integration
async def test_database_pool_context_manager(pg_settings: Settings) -> None:
    """DatabasePool deve funcionar como context manager assíncrono."""
    db = DatabasePool(pg_settings)
    async with db:
        conn = await db.acquire()
        result = await conn.fetchval("SELECT 42")
        assert result == 42
        await db.release(conn)


@pytest.mark.integration
async def test_database_pool_health_check_success(pg_settings: Settings) -> None:
    """health_check deve retornar True quando banco está disponível."""
    db = DatabasePool(pg_settings)
    async with db:
        is_healthy = await db.health_check()
        assert is_healthy is True


@pytest.mark.integration
async def test_database_pool_health_check_failure() -> None:
    """health_check deve retornar False quando banco está inacessível."""
    bad_settings = Settings(
        vertex_project_id="proj",
        database_url="postgresql+asyncpg://bad:bad@localhost:9999/nonexistent",
        _env_file=None,  # type: ignore[call-arg]
    )
    db = DatabasePool(bad_settings)
    is_healthy = await db.health_check()
    assert is_healthy is False


@pytest.mark.integration
async def test_get_pool_returns_initialized_pool(pg_settings: Settings) -> None:
    """get_pool deve retornar pool quando inicializado."""
    db = DatabasePool(pg_settings)
    async with db:
        pool = get_pool(db)
        assert pool is not None
        async with pool.acquire() as conn:
            version = await conn.fetchval("SELECT version()")
            assert "PostgreSQL" in version


@pytest.mark.integration
async def test_pool_min_max_connections(pg_settings: Settings) -> None:
    """Pool deve respeitar os limites min/max de conexões."""
    pool = await create_pool(pg_settings)
    try:
        assert pool.get_min_size() == pg_settings.db_pool_min
        assert pool.get_max_size() == pg_settings.db_pool_max
    finally:
        await pool.close()
