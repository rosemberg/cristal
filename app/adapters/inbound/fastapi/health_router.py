"""Router: Health — GET /api/health (evoluído com pool stats)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

from app.adapters.inbound.fastapi.schemas import HealthResponse, PoolStatus

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["health"])

_VERSION = "2.0.0"


@router.get("/health", response_model=HealthResponse)
async def health_check(request: Request) -> HealthResponse:
    """Verifica saúde da aplicação: banco, pool e estatísticas de dados."""
    # Verificar se o pool está disponível via app.state
    db_pool = getattr(request.app.state, "db_pool", None)
    settings = getattr(request.app.state, "settings", None)

    pool_min = getattr(settings, "db_pool_min", 2) if settings else 2
    pool_max = getattr(settings, "db_pool_max", 10) if settings else 10

    db_connected = False
    pool_size: int | None = None

    if db_pool is not None:
        try:
            db_connected = await db_pool.health_check()
            raw_pool = getattr(db_pool, "_pool", None)
            if raw_pool is not None:
                pool_size = raw_pool.get_size()
        except Exception:  # noqa: BLE001
            logger.exception("Health check: erro ao consultar pool")
            db_connected = False

    stats: dict[str, object] = {}
    search_repo = getattr(request.app.state, "search_repo", None)
    if search_repo is not None:
        try:
            stats = await search_repo.get_stats()
        except Exception:  # noqa: BLE001
            logger.exception("Health check: erro ao obter stats")

    status = "healthy" if db_connected else "degraded"

    return HealthResponse(
        status=status,
        version=_VERSION,
        database=PoolStatus(
            connected=db_connected,
            pool_min=pool_min,
            pool_max=pool_max,
            pool_size=pool_size,
        ),
        stats=stats,
    )
