"""Router: Analytics Admin — GET /api/admin/analytics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.adapters.inbound.fastapi.dependencies import get_analytics_repo
from app.adapters.inbound.fastapi.schemas import (
    AnalyticsResponse,
    DailyStatItem,
    MetricsOut,
)
from app.domain.ports.outbound.analytics_repository import AnalyticsRepository

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/analytics", response_model=AnalyticsResponse)
async def get_analytics(
    days: int = Query(default=30, ge=1, le=365, description="Janela de dias (1–365)"),
    analytics_repo: AnalyticsRepository = Depends(get_analytics_repo),
) -> AnalyticsResponse:
    """Retorna métricas agregadas e stats diárias para o dashboard admin."""
    raw_metrics, raw_stats = await _fetch(analytics_repo, days)

    pos = int(raw_metrics["positive_feedback"])
    neg = int(raw_metrics["negative_feedback"])
    total_fb = pos + neg
    satisfaction_rate = round(pos / total_fb, 2) if total_fb > 0 else 0.0

    metrics = MetricsOut(
        total_queries=int(raw_metrics["total_queries"]),
        avg_response_time_ms=float(raw_metrics["avg_response_time_ms"]),
        positive_feedback=pos,
        negative_feedback=neg,
        satisfaction_rate=satisfaction_rate,
    )

    daily_stats = [
        DailyStatItem(
            date=str(s["date"]),
            query_count=int(s["query_count"]),
            avg_response_time_ms=float(s["avg_response_time_ms"]),
        )
        for s in raw_stats
    ]

    return AnalyticsResponse(metrics=metrics, daily_stats=daily_stats, days=days)


async def _fetch(
    repo: AnalyticsRepository, days: int
) -> tuple[dict[str, object], list[dict[str, object]]]:
    """Busca métricas e stats em paralelo."""
    import asyncio

    metrics, stats = await asyncio.gather(
        repo.get_metrics(days),
        repo.get_daily_stats(days),
    )
    return metrics, stats
