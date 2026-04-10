"""PostgreSQL connection pool — asyncpg.

Fornece:
- create_pool(): cria pool asyncpg diretamente
- DatabasePool: classe com context manager assíncrono, acquire/release e health_check
- get_pool(): extrai o pool interno de um DatabasePool inicializado
"""

from __future__ import annotations

import logging
from types import TracebackType
from typing import TYPE_CHECKING

import asyncpg

from app.config.settings import Settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_ASYNCPG_PREFIX = "postgresql+asyncpg://"
_PLAIN_PREFIX = "postgresql://"


def _to_asyncpg_dsn(database_url: str) -> str:
    """Converte URL SQLAlchemy (postgresql+asyncpg://) para DSN nativo asyncpg."""
    if database_url.startswith(_ASYNCPG_PREFIX):
        return _PLAIN_PREFIX + database_url[len(_ASYNCPG_PREFIX):]
    return database_url


async def create_pool(settings: Settings) -> asyncpg.Pool:
    """Cria e retorna um pool asyncpg configurado com as settings fornecidas."""
    dsn = _to_asyncpg_dsn(settings.database_url)
    pool: asyncpg.Pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
    )
    logger.info(
        "PostgreSQL pool criado: min=%d max=%d",
        settings.db_pool_min,
        settings.db_pool_max,
    )
    return pool


class DatabasePool:
    """Gerencia o ciclo de vida do pool asyncpg via context manager assíncrono.

    Uso:
        db = DatabasePool(settings)
        async with db:
            conn = await db.acquire()
            ...
            await db.release(conn)
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None

    async def __aenter__(self) -> DatabasePool:
        self._pool = await create_pool(self._settings)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Fecha o pool, aguardando todas as conexões serem devolvidas."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("PostgreSQL pool fechado.")

    async def acquire(self) -> asyncpg.Connection:
        """Adquire uma conexão do pool."""
        if self._pool is None:
            msg = "Pool não inicializado. Use 'async with DatabasePool(settings)'."
            raise RuntimeError(msg)
        return await self._pool.acquire()

    async def release(self, conn: asyncpg.Connection) -> None:
        """Devolve uma conexão ao pool."""
        if self._pool is not None:
            await self._pool.release(conn)

    async def health_check(self) -> bool:
        """Testa se o banco está acessível. Retorna False em caso de erro."""
        if self._pool is None:
            # Tenta conexão direta para verificar disponibilidade
            dsn = _to_asyncpg_dsn(self._settings.database_url)
            try:
                conn: asyncpg.Connection = await asyncpg.connect(dsn=dsn, timeout=3)
                await conn.fetchval("SELECT 1")
                await conn.close()
                return True
            except Exception:  # noqa: BLE001
                return False
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            logger.exception("PostgreSQL health check falhou.")
            return False


def get_pool(db: DatabasePool) -> asyncpg.Pool:
    """Extrai o pool interno de um DatabasePool já inicializado.

    Use em contextos onde o pool raw é necessário (ex: routers FastAPI).
    """
    if db._pool is None:  # noqa: SLF001
        msg = "Pool não inicializado."
        raise RuntimeError(msg)
    return db._pool
