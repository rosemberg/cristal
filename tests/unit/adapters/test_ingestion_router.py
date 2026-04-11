"""Unit tests — IngestionRouter (Etapa 7).

Testa todos os endpoints de /api/admin/ingestion/* com serviços mockados.
Usa TestClient síncrono + dependency_overrides.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.adapters.inbound.fastapi.ingestion_router import router as ingestion_router
from app.domain.value_objects.data_inconsistency import DataInconsistency, HealthCheckReport
from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus

# ── Constantes ────────────────────────────────────────────────────────────────

API_KEY = "test-admin-key"
AUTH_HEADER = {"X-Admin-Key": API_KEY}
NOW = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
DOC_URL = "https://www.tre-pi.jus.br/doc/resolucao-456.pdf"


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_ingestion_stats(**kwargs: object) -> IngestionStats:
    defaults = dict(
        total=10, processed=9, errors=1, skipped=0,
        duration_seconds=5.0, inconsistencies_found=1,
    )
    defaults.update(kwargs)
    return IngestionStats(**defaults)  # type: ignore[arg-type]


def make_ingestion_status(**kwargs: object) -> IngestionStatus:
    defaults = dict(
        pending=5, processing=1, done=90, error=4,
        total_chunks=1000, total_tables=50, open_inconsistencies=8,
    )
    defaults.update(kwargs)
    return IngestionStatus(**defaults)  # type: ignore[arg-type]


def make_health_report(**kwargs: object) -> HealthCheckReport:
    defaults = dict(
        total_checked=100, healthy=95, issues_found=5,
        new_inconsistencies=5, updated_inconsistencies=0, auto_resolved=1,
        duration_seconds=30.0, by_type={"broken_link": 3, "page_not_accessible": 2},
    )
    defaults.update(kwargs)
    return HealthCheckReport(**defaults)  # type: ignore[arg-type]


def make_inconsistency(id: int = 1, severity: str = "critical") -> DataInconsistency:
    return DataInconsistency(
        id=id,
        resource_type="document",
        severity=severity,
        inconsistency_type="document_not_found",
        resource_url=DOC_URL,
        resource_title="resolucao-456.pdf",
        parent_page_url="https://www.tre-pi.jus.br/licitacoes",
        detail="HTTP 404",
        http_status=404,
        error_message=None,
        detected_at=NOW,
        detected_by="health_check",
        status="open",
        resolved_at=None,
        resolved_by=None,
        resolution_note=None,
        retry_count=0,
        last_checked_at=NOW,
    )


def make_test_app() -> tuple[FastAPI, AsyncMock, AsyncMock]:
    """Cria app de teste e retorna (app, ingestion_mock, health_check_mock)."""

    @asynccontextmanager
    async def noop_lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        yield

    app = FastAPI(lifespan=noop_lifespan)
    app.include_router(ingestion_router)

    ingestion_mock = AsyncMock()
    health_check_mock = AsyncMock()

    app.state.ingestion_service = ingestion_mock
    app.state.health_check_service = health_check_mock
    app.state.settings = MagicMock(admin_api_key=API_KEY)

    return app, ingestion_mock, health_check_mock


# ── POST /run ─────────────────────────────────────────────────────────────────


class TestTriggerIngestion:
    def test_run_returns_200(self) -> None:
        app, ing, _ = make_test_app()
        ing.ingest_pending = AsyncMock(return_value=make_ingestion_stats())

        with TestClient(app) as client:
            resp = client.post("/api/admin/ingestion/run", headers=AUTH_HEADER)

        assert resp.status_code == 200

    def test_run_calls_ingest_pending_default_concurrency(self) -> None:
        app, ing, _ = make_test_app()
        ing.ingest_pending = AsyncMock(return_value=make_ingestion_stats())

        with TestClient(app) as client:
            client.post("/api/admin/ingestion/run", headers=AUTH_HEADER)

        ing.ingest_pending.assert_awaited_once_with(concurrency=3)

    def test_run_calls_ingest_pending_custom_concurrency(self) -> None:
        app, ing, _ = make_test_app()
        ing.ingest_pending = AsyncMock(return_value=make_ingestion_stats())

        with TestClient(app) as client:
            client.post(
                "/api/admin/ingestion/run?concurrency=5", headers=AUTH_HEADER
            )

        ing.ingest_pending.assert_awaited_once_with(concurrency=5)

    def test_run_response_shape(self) -> None:
        app, ing, _ = make_test_app()
        ing.ingest_pending = AsyncMock(
            return_value=make_ingestion_stats(total=10, processed=9, errors=1)
        )

        with TestClient(app) as client:
            resp = client.post("/api/admin/ingestion/run", headers=AUTH_HEADER)

        data = resp.json()
        assert "total" in data
        assert "processed" in data
        assert "errors" in data
        assert data["total"] == 10
        assert data["processed"] == 9


# ── GET /status ───────────────────────────────────────────────────────────────


class TestIngestionStatus:
    def test_status_returns_200(self) -> None:
        app, ing, _ = make_test_app()
        ing.get_status = AsyncMock(return_value=make_ingestion_status())

        with TestClient(app) as client:
            resp = client.get("/api/admin/ingestion/status", headers=AUTH_HEADER)

        assert resp.status_code == 200

    def test_status_calls_get_status(self) -> None:
        app, ing, _ = make_test_app()
        ing.get_status = AsyncMock(return_value=make_ingestion_status())

        with TestClient(app) as client:
            client.get("/api/admin/ingestion/status", headers=AUTH_HEADER)

        ing.get_status.assert_awaited_once()

    def test_status_response_shape(self) -> None:
        app, ing, _ = make_test_app()
        ing.get_status = AsyncMock(
            return_value=make_ingestion_status(pending=5, done=90, open_inconsistencies=8)
        )

        with TestClient(app) as client:
            resp = client.get("/api/admin/ingestion/status", headers=AUTH_HEADER)

        data = resp.json()
        assert data["pending"] == 5
        assert data["done"] == 90
        assert data["open_inconsistencies"] == 8


# ── POST /reprocess ───────────────────────────────────────────────────────────


class TestReprocessErrors:
    def test_reprocess_returns_200(self) -> None:
        app, ing, _ = make_test_app()
        ing.reprocess_errors = AsyncMock(return_value=make_ingestion_stats())

        with TestClient(app) as client:
            resp = client.post("/api/admin/ingestion/reprocess", headers=AUTH_HEADER)

        assert resp.status_code == 200

    def test_reprocess_calls_reprocess_errors(self) -> None:
        app, ing, _ = make_test_app()
        ing.reprocess_errors = AsyncMock(return_value=make_ingestion_stats())

        with TestClient(app) as client:
            client.post("/api/admin/ingestion/reprocess", headers=AUTH_HEADER)

        ing.reprocess_errors.assert_awaited_once()


# ── POST /single/{url} ────────────────────────────────────────────────────────


class TestIngestSingle:
    def test_single_returns_200_on_success(self) -> None:
        app, ing, _ = make_test_app()
        ing.ingest_single = AsyncMock(return_value=True)

        with TestClient(app) as client:
            resp = client.post(
                f"/api/admin/ingestion/single/{DOC_URL}",
                headers=AUTH_HEADER,
            )

        assert resp.status_code == 200

    def test_single_calls_ingest_single_with_url(self) -> None:
        app, ing, _ = make_test_app()
        ing.ingest_single = AsyncMock(return_value=True)

        with TestClient(app) as client:
            client.post(
                f"/api/admin/ingestion/single/{DOC_URL}",
                headers=AUTH_HEADER,
            )

        ing.ingest_single.assert_awaited_once_with(DOC_URL)

    def test_single_response_success_true(self) -> None:
        app, ing, _ = make_test_app()
        ing.ingest_single = AsyncMock(return_value=True)

        with TestClient(app) as client:
            resp = client.post(
                f"/api/admin/ingestion/single/{DOC_URL}",
                headers=AUTH_HEADER,
            )

        data = resp.json()
        assert data["success"] is True
        assert "url" in data

    def test_single_response_success_false(self) -> None:
        app, ing, _ = make_test_app()
        ing.ingest_single = AsyncMock(return_value=False)

        with TestClient(app) as client:
            resp = client.post(
                f"/api/admin/ingestion/single/{DOC_URL}",
                headers=AUTH_HEADER,
            )

        data = resp.json()
        assert data["success"] is False


# ── POST /health-check ────────────────────────────────────────────────────────


class TestTriggerHealthCheck:
    def test_health_check_returns_200(self) -> None:
        app, _, hc = make_test_app()
        hc.check_all = AsyncMock(return_value=make_health_report())

        with TestClient(app) as client:
            resp = client.post(
                "/api/admin/ingestion/health-check", headers=AUTH_HEADER
            )

        assert resp.status_code == 200

    def test_health_check_default_calls_check_all(self) -> None:
        app, _, hc = make_test_app()
        hc.check_all = AsyncMock(return_value=make_health_report())

        with TestClient(app) as client:
            client.post("/api/admin/ingestion/health-check", headers=AUTH_HEADER)

        hc.check_all.assert_awaited_once()

    def test_health_check_pages_type(self) -> None:
        app, _, hc = make_test_app()
        hc.check_pages = AsyncMock(return_value=make_health_report())

        with TestClient(app) as client:
            client.post(
                "/api/admin/ingestion/health-check?check_type=pages",
                headers=AUTH_HEADER,
            )

        hc.check_pages.assert_awaited_once()

    def test_health_check_documents_type(self) -> None:
        app, _, hc = make_test_app()
        hc.check_documents = AsyncMock(return_value=make_health_report())

        with TestClient(app) as client:
            client.post(
                "/api/admin/ingestion/health-check?check_type=documents",
                headers=AUTH_HEADER,
            )

        hc.check_documents.assert_awaited_once()

    def test_health_check_links_type(self) -> None:
        app, _, hc = make_test_app()
        hc.check_links = AsyncMock(return_value=make_health_report())

        with TestClient(app) as client:
            client.post(
                "/api/admin/ingestion/health-check?check_type=links",
                headers=AUTH_HEADER,
            )

        hc.check_links.assert_awaited_once()

    def test_health_check_response_shape(self) -> None:
        app, _, hc = make_test_app()
        hc.check_all = AsyncMock(return_value=make_health_report(total_checked=100, auto_resolved=1))

        with TestClient(app) as client:
            resp = client.post(
                "/api/admin/ingestion/health-check", headers=AUTH_HEADER
            )

        data = resp.json()
        assert data["total_checked"] == 100
        assert data["auto_resolved"] == 1
        assert "by_type" in data


# ── GET /inconsistencies ──────────────────────────────────────────────────────


class TestListInconsistencies:
    def test_inconsistencies_returns_200(self) -> None:
        app, _, hc = make_test_app()
        hc.get_inconsistencies = AsyncMock(return_value=[])

        with TestClient(app) as client:
            resp = client.get(
                "/api/admin/ingestion/inconsistencies", headers=AUTH_HEADER
            )

        assert resp.status_code == 200

    def test_inconsistencies_calls_get_inconsistencies_defaults(self) -> None:
        app, _, hc = make_test_app()
        hc.get_inconsistencies = AsyncMock(return_value=[])

        with TestClient(app) as client:
            client.get(
                "/api/admin/ingestion/inconsistencies", headers=AUTH_HEADER
            )

        hc.get_inconsistencies.assert_awaited_once_with(
            status="open",
            resource_type=None,
            severity=None,
            limit=50,
            offset=0,
        )

    def test_inconsistencies_passes_filters(self) -> None:
        app, _, hc = make_test_app()
        hc.get_inconsistencies = AsyncMock(return_value=[])

        with TestClient(app) as client:
            client.get(
                "/api/admin/ingestion/inconsistencies"
                "?status=resolved&severity=critical&resource_type=document&limit=10&offset=5",
                headers=AUTH_HEADER,
            )

        hc.get_inconsistencies.assert_awaited_once_with(
            status="resolved",
            resource_type="document",
            severity="critical",
            limit=10,
            offset=5,
        )

    def test_inconsistencies_returns_list(self) -> None:
        app, _, hc = make_test_app()
        hc.get_inconsistencies = AsyncMock(
            return_value=[make_inconsistency(id=1), make_inconsistency(id=2)]
        )

        with TestClient(app) as client:
            resp = client.get(
                "/api/admin/ingestion/inconsistencies", headers=AUTH_HEADER
            )

        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["id"] == 1
        assert data[0]["severity"] == "critical"
        assert data[0]["inconsistency_type"] == "document_not_found"


# ── GET /inconsistencies/summary ──────────────────────────────────────────────


class TestInconsistencySummary:
    def test_summary_returns_200(self) -> None:
        app, _, hc = make_test_app()
        hc.get_inconsistencies = AsyncMock(
            return_value=[
                make_inconsistency(id=1, severity="critical"),
                make_inconsistency(id=2, severity="warning"),
            ]
        )

        with TestClient(app) as client:
            resp = client.get(
                "/api/admin/ingestion/inconsistencies/summary", headers=AUTH_HEADER
            )

        assert resp.status_code == 200

    def test_summary_response_has_counts_by_severity(self) -> None:
        app, _, hc = make_test_app()
        hc.get_inconsistencies = AsyncMock(
            return_value=[
                make_inconsistency(id=1, severity="critical"),
                make_inconsistency(id=2, severity="critical"),
                make_inconsistency(id=3, severity="warning"),
            ]
        )

        with TestClient(app) as client:
            resp = client.get(
                "/api/admin/ingestion/inconsistencies/summary", headers=AUTH_HEADER
            )

        data = resp.json()
        assert "total" in data
        assert data["total"] == 3
        assert "by_severity" in data
        assert data["by_severity"]["critical"] == 2
        assert data["by_severity"]["warning"] == 1


# ── PATCH /inconsistencies/{id}/resolve ───────────────────────────────────────


class TestResolveInconsistency:
    def test_resolve_returns_200(self) -> None:
        app, _, hc = make_test_app()
        hc.resolve_inconsistency = AsyncMock(return_value=None)

        with TestClient(app) as client:
            resp = client.patch(
                "/api/admin/ingestion/inconsistencies/1/resolve"
                "?resolution_note=Corrigido&resolved_by=admin",
                headers=AUTH_HEADER,
            )

        assert resp.status_code == 200

    def test_resolve_calls_service(self) -> None:
        app, _, hc = make_test_app()
        hc.resolve_inconsistency = AsyncMock(return_value=None)

        with TestClient(app) as client:
            client.patch(
                "/api/admin/ingestion/inconsistencies/1/resolve"
                "?resolution_note=Corrigido&resolved_by=admin",
                headers=AUTH_HEADER,
            )

        hc.resolve_inconsistency.assert_awaited_once_with(
            inconsistency_id=1,
            resolution_note="Corrigido",
            resolved_by="admin",
        )

    def test_resolve_response_shape(self) -> None:
        app, _, hc = make_test_app()
        hc.resolve_inconsistency = AsyncMock(return_value=None)

        with TestClient(app) as client:
            resp = client.patch(
                "/api/admin/ingestion/inconsistencies/1/resolve"
                "?resolution_note=OK&resolved_by=admin",
                headers=AUTH_HEADER,
            )

        data = resp.json()
        assert data["ok"] is True


# ── PATCH /inconsistencies/{id}/acknowledge ───────────────────────────────────


class TestAcknowledgeInconsistency:
    def test_acknowledge_returns_200(self) -> None:
        app, _, hc = make_test_app()
        hc.acknowledge_inconsistency = AsyncMock(return_value=None)

        with TestClient(app) as client:
            resp = client.patch(
                "/api/admin/ingestion/inconsistencies/1/acknowledge",
                headers=AUTH_HEADER,
            )

        assert resp.status_code == 200

    def test_acknowledge_calls_service(self) -> None:
        app, _, hc = make_test_app()
        hc.acknowledge_inconsistency = AsyncMock(return_value=None)

        with TestClient(app) as client:
            client.patch(
                "/api/admin/ingestion/inconsistencies/1/acknowledge",
                headers=AUTH_HEADER,
            )

        hc.acknowledge_inconsistency.assert_awaited_once_with(inconsistency_id=1)


# ── PATCH /inconsistencies/{id}/ignore ───────────────────────────────────────


class TestIgnoreInconsistency:
    def test_ignore_returns_200(self) -> None:
        app, _, hc = make_test_app()
        hc.ignore_inconsistency = AsyncMock(return_value=None)

        with TestClient(app) as client:
            resp = client.patch(
                "/api/admin/ingestion/inconsistencies/1/ignore?reason=Obsoleto",
                headers=AUTH_HEADER,
            )

        assert resp.status_code == 200

    def test_ignore_calls_service(self) -> None:
        app, _, hc = make_test_app()
        hc.ignore_inconsistency = AsyncMock(return_value=None)

        with TestClient(app) as client:
            client.patch(
                "/api/admin/ingestion/inconsistencies/1/ignore?reason=Obsoleto",
                headers=AUTH_HEADER,
            )

        hc.ignore_inconsistency.assert_awaited_once_with(
            inconsistency_id=1, reason="Obsoleto"
        )
