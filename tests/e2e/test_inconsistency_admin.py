"""Testes E2E — Admin API de inconsistências via FastAPI (Etapa 9 Pipeline V2).

Critérios de aceite:
- Admin lista inconsistências via GET /api/admin/ingestion/inconsistencies
- Admin resolve inconsistência → status muda para 'resolved'
- Admin reconhece inconsistência → status muda para 'acknowledged'
- Admin ignora inconsistência → status muda para 'ignored' com razão
- GET /api/admin/ingestion/inconsistencies/summary → retorna contagens corretas
- GET /api/admin/ingestion/status → retorna contadores do pipeline
- Endpoints sem X-Admin-Key retornam 403
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.adapters.inbound.fastapi.app import create_app
from app.adapters.inbound.fastapi.dependencies import (
    get_health_check_service,
    get_ingestion_service,
)
from app.adapters.inbound.fastapi.ingestion_router import verify_admin_key
from app.adapters.outbound.postgres.document_repo import PostgresDocumentRepository
from app.adapters.outbound.postgres.inconsistency_repo import PostgresInconsistencyRepository
from app.adapters.outbound.postgres.page_repo import PostgresPageRepository
from app.config.settings import Settings
from app.domain.services.data_health_check_service import DataHealthCheckService
from app.domain.services.document_ingestion_service import DocumentIngestionService
from app.domain.value_objects.data_inconsistency import DataInconsistency
from tests.conftest import FakeDocumentProcessGateway
from tests.e2e.conftest import FakeDownloadGateway, docker_required

ADMIN_KEY = "test-admin-key-e2e"


# ─── Fixtures do app ──────────────────────────────────────────────────────────


def _make_settings() -> Settings:
    return Settings(
        vertex_project_id="test-project",
        admin_api_key=ADMIN_KEY,
        database_url="postgresql+asyncpg://localhost/dummy",
        _env_file=None,  # type: ignore[call-arg]
    )


def _make_ingestion_service(pool: asyncpg.Pool) -> DocumentIngestionService:
    return DocumentIngestionService(
        doc_repo=PostgresDocumentRepository(pool),
        downloader=FakeDownloadGateway(),
        processor=FakeDocumentProcessGateway(),
        inconsistency_repo=PostgresInconsistencyRepository(pool),
    )


def _make_health_service(pool: asyncpg.Pool) -> DataHealthCheckService:
    return DataHealthCheckService(
        downloader=FakeDownloadGateway(),
        page_repo=PostgresPageRepository(pool),
        doc_repo=PostgresDocumentRepository(pool),
        inconsistency_repo=PostgresInconsistencyRepository(pool),
        request_delay_ms=0.0,
    )


@pytest.fixture
async def admin_client(pool_e2e: asyncpg.Pool) -> AsyncGenerator[AsyncClient, None]:
    """Cliente HTTP para o app FastAPI com serviços reais injetados via overrides."""
    settings = _make_settings()
    ingestion_svc = _make_ingestion_service(pool_e2e)
    health_svc = _make_health_service(pool_e2e)

    @asynccontextmanager
    async def _noop_lifespan(app):  # type: ignore[no-untyped-def]
        yield

    app = create_app(lifespan=_noop_lifespan)

    # Injecta settings diretamente no state (antes de iniciar o cliente)
    app.state.settings = settings

    # Injeta serviços reais (com pool para o testcontainer)
    app.dependency_overrides[get_ingestion_service] = lambda: ingestion_svc
    app.dependency_overrides[get_health_check_service] = lambda: health_svc

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


# ─── Helpers de seed ──────────────────────────────────────────────────────────


def _make_inconsistency_record(
    resource_url: str = "https://www.tre-pi.jus.br/pagina",
    inconsistency_type: str = "page_not_accessible",
    resource_type: str = "page",
    severity: str = "warning",
) -> DataInconsistency:
    now = datetime.now(timezone.utc)
    return DataInconsistency(
        id=None,
        resource_type=resource_type,
        severity=severity,
        inconsistency_type=inconsistency_type,
        resource_url=resource_url,
        resource_title="Página de Teste",
        parent_page_url=None,
        detail="HTTP 404",
        http_status=404,
        error_message="Not Found",
        detected_at=now,
        detected_by="health_check",
        status="open",
        resolved_at=None,
        resolved_by=None,
        resolution_note=None,
        retry_count=0,
        last_checked_at=now,
    )


async def _seed_inconsistencies(
    pool: asyncpg.Pool, count: int = 3
) -> list[int]:
    """Insere `count` inconsistências e retorna seus IDs."""
    repo = PostgresInconsistencyRepository(pool)
    ids = []
    for i in range(count):
        inc = _make_inconsistency_record(
            resource_url=f"https://www.tre-pi.jus.br/pagina-{i}",
            severity="critical" if i == 0 else "warning",
        )
        row_id = await repo.save(inc)
        ids.append(row_id)
    return ids


# ─── Testes: autenticação ─────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_endpoint_sem_admin_key_retorna_403(
    admin_client: AsyncClient,
) -> None:
    """Endpoint sem X-Admin-Key deve retornar 403."""
    resp = await admin_client.get("/api/admin/ingestion/inconsistencies")
    assert resp.status_code == 403


@docker_required
@pytest.mark.integration
async def test_endpoint_com_chave_incorreta_retorna_403(
    admin_client: AsyncClient,
) -> None:
    """Chave incorreta deve retornar 403."""
    resp = await admin_client.get(
        "/api/admin/ingestion/inconsistencies",
        headers={"X-Admin-Key": "chave-errada"},
    )
    assert resp.status_code == 403


# ─── Testes: listar inconsistências ───────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_list_inconsistencies_retorna_lista(
    pool_e2e: asyncpg.Pool,
    admin_client: AsyncClient,
) -> None:
    """GET /inconsistencies deve retornar lista de inconsistências abertas."""
    await _seed_inconsistencies(pool_e2e, count=3)

    resp = await admin_client.get(
        "/api/admin/ingestion/inconsistencies",
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 3


@docker_required
@pytest.mark.integration
async def test_list_inconsistencies_filtra_por_status(
    pool_e2e: asyncpg.Pool,
    admin_client: AsyncClient,
) -> None:
    """GET /inconsistencies?status=resolved deve retornar apenas resolvidas."""
    ids = await _seed_inconsistencies(pool_e2e, count=2)

    # Resolve a primeira
    repo = PostgresInconsistencyRepository(pool_e2e)
    await repo.update_status(ids[0], "resolved", resolved_by="admin", resolution_note="Corrigido")

    resp = await admin_client.get(
        "/api/admin/ingestion/inconsistencies?status=resolved",
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["status"] == "resolved"


# ─── Testes: resolver inconsistência ─────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_resolve_inconsistency_muda_status_para_resolved(
    pool_e2e: asyncpg.Pool,
    admin_client: AsyncClient,
) -> None:
    """PATCH /inconsistencies/{id}/resolve → status='resolved'."""
    ids = await _seed_inconsistencies(pool_e2e, count=1)
    inc_id = ids[0]

    resp = await admin_client.patch(
        f"/api/admin/ingestion/inconsistencies/{inc_id}/resolve",
        params={"resolution_note": "Página restaurada", "resolved_by": "admin@tre-pi.jus.br"},
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    async with pool_e2e.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, resolved_by, resolution_note FROM data_inconsistencies WHERE id = $1",
            inc_id,
        )
    assert row["status"] == "resolved"
    assert row["resolved_by"] == "admin@tre-pi.jus.br"
    assert row["resolution_note"] == "Página restaurada"


# ─── Testes: reconhecer inconsistência ────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_acknowledge_inconsistency_muda_status_para_acknowledged(
    pool_e2e: asyncpg.Pool,
    admin_client: AsyncClient,
) -> None:
    """PATCH /inconsistencies/{id}/acknowledge → status='acknowledged'."""
    ids = await _seed_inconsistencies(pool_e2e, count=1)
    inc_id = ids[0]

    resp = await admin_client.patch(
        f"/api/admin/ingestion/inconsistencies/{inc_id}/acknowledge",
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    assert resp.status_code == 200

    async with pool_e2e.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM data_inconsistencies WHERE id = $1", inc_id
        )
    assert status == "acknowledged"


# ─── Testes: ignorar inconsistência ───────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_ignore_inconsistency_muda_status_para_ignored(
    pool_e2e: asyncpg.Pool,
    admin_client: AsyncClient,
) -> None:
    """PATCH /inconsistencies/{id}/ignore → status='ignored' com razão."""
    ids = await _seed_inconsistencies(pool_e2e, count=1)
    inc_id = ids[0]

    resp = await admin_client.patch(
        f"/api/admin/ingestion/inconsistencies/{inc_id}/ignore",
        params={"reason": "Página de teste — não monitorar"},
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    assert resp.status_code == 200

    async with pool_e2e.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, resolution_note FROM data_inconsistencies WHERE id = $1",
            inc_id,
        )
    assert row["status"] == "ignored"
    assert row["resolution_note"] == "Página de teste — não monitorar"


# ─── Testes: resumo ───────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_inconsistency_summary_retorna_totais(
    pool_e2e: asyncpg.Pool,
    admin_client: AsyncClient,
) -> None:
    """GET /inconsistencies/summary → retorna totais por severidade e tipo."""
    await _seed_inconsistencies(pool_e2e, count=3)

    resp = await admin_client.get(
        "/api/admin/ingestion/inconsistencies/summary",
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert "by_severity" in data
    assert "by_type" in data
    assert "by_resource_type" in data
    # 1 critical + 2 warning (conforme _seed_inconsistencies)
    assert data["by_severity"].get("critical", 0) == 1
    assert data["by_severity"].get("warning", 0) == 2


# ─── Testes: status do pipeline ───────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_ingestion_status_retorna_contadores(
    pool_e2e: asyncpg.Pool,
    admin_client: AsyncClient,
) -> None:
    """GET /status → retorna contadores do pipeline."""
    # Insere uma página e dois documentos (um pendente, um processado)
    async with pool_e2e.acquire() as conn:
        await conn.execute(
            "INSERT INTO pages (url, title) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            "https://www.tre-pi.jus.br/base", "Página Base",
        )
        await conn.execute(
            """
            INSERT INTO documents (page_url, document_url, document_title, document_type, processing_status)
            VALUES ($1, $2, $3, 'pdf', 'pending')
            ON CONFLICT DO NOTHING
            """,
            "https://www.tre-pi.jus.br/base",
            "https://www.tre-pi.jus.br/docs/pendente.pdf",
            "Documento Pendente",
        )
        await conn.execute(
            """
            INSERT INTO documents (page_url, document_url, document_title, document_type, processing_status)
            VALUES ($1, $2, $3, 'pdf', 'done')
            ON CONFLICT DO NOTHING
            """,
            "https://www.tre-pi.jus.br/base",
            "https://www.tre-pi.jus.br/docs/processado.pdf",
            "Documento Processado",
        )

    resp = await admin_client.get(
        "/api/admin/ingestion/status",
        headers={"X-Admin-Key": ADMIN_KEY},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["pending"] == 1
    assert data["done"] == 1
    assert "total_chunks" in data
    assert "open_inconsistencies" in data
