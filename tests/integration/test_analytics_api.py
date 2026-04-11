"""Integration tests — Analytics API (RED → GREEN).

Testa GET /api/admin/analytics usando fake AnalyticsRepository injetado via
dependency_overrides. Nenhum banco real é chamado.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.ports.outbound.analytics_repository import AnalyticsRepository


# ---------------------------------------------------------------------------
# Fake
# ---------------------------------------------------------------------------


class _FakeAnalyticsRepo(AnalyticsRepository):
    """Fake imutável com dados fixos para testes."""

    async def log_query(
        self,
        session_id: UUID | None,
        query: str,
        intent_type: str,
        pages_found: int,
        chunks_found: int,
        tables_found: int,
        response_time_ms: int,
    ) -> int:
        return 1

    async def update_feedback(self, query_id: int, feedback: str) -> None:
        return None

    async def get_metrics(self, days: int = 30) -> dict[str, object]:
        return {
            "total_queries": 42,
            "avg_response_time_ms": 850.0,
            "positive_feedback": 30,
            "negative_feedback": 5,
        }

    async def get_daily_stats(self, days: int = 30) -> list[dict[str, object]]:
        return [
            {"date": "2026-04-11", "query_count": 15, "avg_response_time_ms": 800.0},
            {"date": "2026-04-10", "query_count": 27, "avg_response_time_ms": 890.0},
        ]


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from app.adapters.inbound.fastapi.app import create_app
    from app.adapters.inbound.fastapi.dependencies import get_analytics_repo

    @asynccontextmanager
    async def noop_lifespan(app):  # type: ignore[no-untyped-def]
        yield

    app = create_app(lifespan=noop_lifespan)
    app.dependency_overrides[get_analytics_repo] = lambda: _FakeAnalyticsRepo()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_analytics_returns_200(client: AsyncClient) -> None:
    """GET /api/admin/analytics retorna 200 OK."""
    resp = await client.get("/api/admin/analytics")
    assert resp.status_code == 200


@pytest.mark.anyio
async def test_analytics_default_days(client: AsyncClient) -> None:
    """Sem parâmetro days, usa 30 por padrão."""
    resp = await client.get("/api/admin/analytics")
    data = resp.json()
    assert "metrics" in data
    assert "daily_stats" in data
    assert data["days"] == 30


@pytest.mark.anyio
async def test_analytics_custom_days(client: AsyncClient) -> None:
    """Parâmetro days= é refletido na resposta."""
    resp = await client.get("/api/admin/analytics?days=7")
    assert resp.status_code == 200
    assert resp.json()["days"] == 7


@pytest.mark.anyio
async def test_analytics_metrics_fields(client: AsyncClient) -> None:
    """Métricas contêm todos os campos esperados."""
    resp = await client.get("/api/admin/analytics")
    metrics = resp.json()["metrics"]
    assert metrics["total_queries"] == 42
    assert metrics["avg_response_time_ms"] == 850.0
    assert metrics["positive_feedback"] == 30
    assert metrics["negative_feedback"] == 5
    # taxa de satisfação calculada no servidor
    assert "satisfaction_rate" in metrics


@pytest.mark.anyio
async def test_analytics_satisfaction_rate_calculation(client: AsyncClient) -> None:
    """satisfaction_rate = positive / (positive + negative), arredondado 2 casas."""
    resp = await client.get("/api/admin/analytics")
    metrics = resp.json()["metrics"]
    # 30 / (30 + 5) ≈ 0.857142… → 0.86
    assert abs(metrics["satisfaction_rate"] - round(30 / 35, 2)) < 0.01


@pytest.mark.anyio
async def test_analytics_daily_stats_structure(client: AsyncClient) -> None:
    """daily_stats é lista com itens data/query_count/avg_response_time_ms."""
    resp = await client.get("/api/admin/analytics")
    stats = resp.json()["daily_stats"]
    assert isinstance(stats, list)
    assert len(stats) == 2
    first = stats[0]
    assert "date" in first
    assert "query_count" in first
    assert "avg_response_time_ms" in first


@pytest.mark.anyio
async def test_analytics_days_validation_too_low(client: AsyncClient) -> None:
    """days < 1 retorna 422 Unprocessable Entity."""
    resp = await client.get("/api/admin/analytics?days=0")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_analytics_days_validation_too_high(client: AsyncClient) -> None:
    """days > 365 retorna 422 Unprocessable Entity."""
    resp = await client.get("/api/admin/analytics?days=366")
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_analytics_zero_feedback_satisfaction_rate(client: AsyncClient) -> None:
    """Com zero feedbacks, satisfaction_rate deve ser 0.0 sem divisão por zero."""

    class _ZeroFeedbackRepo(_FakeAnalyticsRepo):
        async def get_metrics(self, days: int = 30) -> dict[str, object]:
            return {
                "total_queries": 10,
                "avg_response_time_ms": 500.0,
                "positive_feedback": 0,
                "negative_feedback": 0,
            }

    from app.adapters.inbound.fastapi.app import create_app
    from app.adapters.inbound.fastapi.dependencies import get_analytics_repo

    @asynccontextmanager
    async def noop(app):  # type: ignore[no-untyped-def]
        yield

    app2 = create_app(lifespan=noop)
    app2.dependency_overrides[get_analytics_repo] = lambda: _ZeroFeedbackRepo()

    async with AsyncClient(
        transport=ASGITransport(app=app2), base_url="http://test"
    ) as c2:
        resp = await c2.get("/api/admin/analytics")
        assert resp.status_code == 200
        assert resp.json()["metrics"]["satisfaction_rate"] == 0.0
