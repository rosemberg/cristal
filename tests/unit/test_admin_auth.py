"""Unit tests — Autenticação admin via X-Admin-Key (Etapa 7).

Verifica que todos os endpoints de ingestão exigem o header X-Admin-Key
e retornam 403 sem ele ou com key incorreta.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.inbound.fastapi.ingestion_router import router as ingestion_router
from app.domain.value_objects.data_inconsistency import DataInconsistency, HealthCheckReport
from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus


# ── App de teste ──────────────────────────────────────────────────────────────


def make_test_app(admin_api_key: str = "secret-key") -> FastAPI:
    """Cria app FastAPI mínimo com ingestion_router e estado mockado."""

    @asynccontextmanager
    async def noop_lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        yield

    app = FastAPI(lifespan=noop_lifespan)
    app.include_router(ingestion_router)

    # Injetar mocks em app.state
    app.state.ingestion_service = _make_ingestion_mock()
    app.state.health_check_service = _make_health_check_mock()
    app.state.settings = MagicMock(admin_api_key=admin_api_key)

    return app


def _make_ingestion_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.ingest_pending = AsyncMock(
        return_value=IngestionStats(
            total=10, processed=9, errors=1, skipped=0,
            duration_seconds=5.0, inconsistencies_found=1,
        )
    )
    mock.get_status = AsyncMock(
        return_value=IngestionStatus(
            pending=5, processing=1, done=90, error=4,
            total_chunks=1000, total_tables=50, open_inconsistencies=8,
        )
    )
    mock.reprocess_errors = AsyncMock(
        return_value=IngestionStats(
            total=4, processed=3, errors=1, skipped=0,
            duration_seconds=2.0, inconsistencies_found=0,
        )
    )
    mock.ingest_single = AsyncMock(return_value=True)
    return mock


def _make_health_check_mock() -> AsyncMock:
    mock = AsyncMock()
    mock.check_all = AsyncMock(
        return_value=HealthCheckReport(
            total_checked=100, healthy=95, issues_found=5,
            new_inconsistencies=5, updated_inconsistencies=0, auto_resolved=1,
            duration_seconds=30.0, by_type={"broken_link": 3, "page_not_accessible": 2},
        )
    )
    mock.check_pages = AsyncMock(
        return_value=HealthCheckReport(
            total_checked=50, healthy=48, issues_found=2,
            new_inconsistencies=2, updated_inconsistencies=0, auto_resolved=0,
            duration_seconds=10.0, by_type={"page_not_accessible": 2},
        )
    )
    mock.get_inconsistencies = AsyncMock(return_value=[])
    mock.resolve_inconsistency = AsyncMock(return_value=None)
    mock.acknowledge_inconsistency = AsyncMock(return_value=None)
    mock.ignore_inconsistency = AsyncMock(return_value=None)
    return mock


# ── Endpoints sujeitos à autenticação ─────────────────────────────────────────

PROTECTED_ENDPOINTS = [
    ("POST", "/api/admin/ingestion/run"),
    ("GET", "/api/admin/ingestion/status"),
    ("POST", "/api/admin/ingestion/reprocess"),
    ("POST", "/api/admin/ingestion/single/https://example.com/doc.pdf"),
    ("POST", "/api/admin/ingestion/health-check"),
    ("GET", "/api/admin/ingestion/inconsistencies"),
    ("GET", "/api/admin/ingestion/inconsistencies/summary"),
    ("PATCH", "/api/admin/ingestion/inconsistencies/1/resolve"),
    ("PATCH", "/api/admin/ingestion/inconsistencies/1/acknowledge"),
    ("PATCH", "/api/admin/ingestion/inconsistencies/1/ignore"),
]


class TestAdminAuthRequired:
    """Todos os endpoints retornam 403 sem o header X-Admin-Key."""

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_missing_key_returns_403(self, method: str, path: str) -> None:
        app = make_test_app(admin_api_key="secret-key")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = getattr(client, method.lower())(path)
            assert resp.status_code == 403, (
                f"{method} {path} retornou {resp.status_code}, esperado 403"
            )

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_wrong_key_returns_403(self, method: str, path: str) -> None:
        app = make_test_app(admin_api_key="correct-key")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = getattr(client, method.lower())(
                path, headers={"X-Admin-Key": "wrong-key"}
            )
            assert resp.status_code == 403, (
                f"{method} {path} com key errada retornou {resp.status_code}, esperado 403"
            )

    @pytest.mark.parametrize("method,path", [
        ("POST", "/api/admin/ingestion/run"),
        ("GET", "/api/admin/ingestion/status"),
        ("GET", "/api/admin/ingestion/inconsistencies"),
    ])
    def test_correct_key_passes_auth(self, method: str, path: str) -> None:
        app = make_test_app(admin_api_key="correct-key")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = getattr(client, method.lower())(
                path, headers={"X-Admin-Key": "correct-key"}
            )
            # Qualquer coisa diferente de 403 significa que a auth passou
            assert resp.status_code != 403, (
                f"{method} {path} com key correta retornou 403 (auth falhou)"
            )

    def test_empty_key_returns_403(self) -> None:
        app = make_test_app(admin_api_key="secret-key")
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get(
                "/api/admin/ingestion/status",
                headers={"X-Admin-Key": ""},
            )
            assert resp.status_code == 403
