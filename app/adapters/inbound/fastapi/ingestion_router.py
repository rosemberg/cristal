"""Router: Ingestion Admin — /api/admin/ingestion.

Endpoints para disparar ingestão de documentos, health check e gerenciar
inconsistências. Todos exigem autenticação via header X-Admin-Key.

[V2] Prefix /api/admin/ingestion (não conflita com analytics em /api/admin/analytics).
"""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, Depends, Header, HTTPException, Request

from app.adapters.inbound.fastapi.dependencies import (
    get_health_check_service,
    get_ingestion_service,
)
from app.adapters.inbound.fastapi.schemas import (
    DataInconsistencyOut,
    HealthCheckReportOut,
    InconsistencySummaryOut,
    IngestionStatsOut,
    IngestionStatusOut,
)
from app.domain.ports.inbound.data_health_check_use_case import DataHealthCheckUseCase
from app.domain.ports.inbound.document_ingestion_use_case import DocumentIngestionUseCase
from app.domain.value_objects.data_inconsistency import DataInconsistency, HealthCheckReport
from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus

router = APIRouter(prefix="/api/admin/ingestion", tags=["ingestion"])


# ── Autenticação ──────────────────────────────────────────────────────────────


async def verify_admin_key(
    request: Request,
    x_admin_key: str | None = Header(default=None),
) -> None:
    """Verifica API key do admin via header X-Admin-Key. Retorna 403 se ausente ou inválida."""
    expected = request.app.state.settings.admin_api_key
    if not x_admin_key or x_admin_key != expected:
        raise HTTPException(status_code=403, detail="Invalid or missing admin key")


# ── Converters ────────────────────────────────────────────────────────────────


def _stats_to_out(stats: IngestionStats) -> IngestionStatsOut:
    return IngestionStatsOut(
        total=stats.total,
        processed=stats.processed,
        errors=stats.errors,
        skipped=stats.skipped,
        duration_seconds=stats.duration_seconds,
        inconsistencies_found=stats.inconsistencies_found,
    )


def _status_to_out(status: IngestionStatus) -> IngestionStatusOut:
    return IngestionStatusOut(
        pending=status.pending,
        processing=status.processing,
        done=status.done,
        error=status.error,
        total_chunks=status.total_chunks,
        total_tables=status.total_tables,
        open_inconsistencies=status.open_inconsistencies,
    )


def _report_to_out(report: HealthCheckReport) -> HealthCheckReportOut:
    return HealthCheckReportOut(
        total_checked=report.total_checked,
        healthy=report.healthy,
        issues_found=report.issues_found,
        new_inconsistencies=report.new_inconsistencies,
        updated_inconsistencies=report.updated_inconsistencies,
        auto_resolved=report.auto_resolved,
        duration_seconds=report.duration_seconds,
        by_type=report.by_type,
    )


def _inconsistency_to_out(inc: DataInconsistency) -> DataInconsistencyOut:
    return DataInconsistencyOut(
        id=inc.id,
        resource_type=inc.resource_type,
        severity=inc.severity,
        inconsistency_type=inc.inconsistency_type,
        resource_url=inc.resource_url,
        resource_title=inc.resource_title,
        parent_page_url=inc.parent_page_url,
        detail=inc.detail,
        http_status=inc.http_status,
        error_message=inc.error_message,
        detected_at=inc.detected_at,
        detected_by=inc.detected_by,
        status=inc.status,
        resolved_at=inc.resolved_at,
        resolved_by=inc.resolved_by,
        resolution_note=inc.resolution_note,
        retry_count=inc.retry_count,
        last_checked_at=inc.last_checked_at,
    )


# ── Ingestão ──────────────────────────────────────────────────────────────────


@router.post("/run", response_model=IngestionStatsOut, dependencies=[Depends(verify_admin_key)])
async def trigger_ingestion(
    concurrency: int = 3,
    service: DocumentIngestionUseCase = Depends(get_ingestion_service),
) -> IngestionStatsOut:
    """Dispara ingestão de todos os documentos pendentes."""
    stats = await service.ingest_pending(concurrency=concurrency)
    return _stats_to_out(stats)


@router.get("/status", response_model=IngestionStatusOut, dependencies=[Depends(verify_admin_key)])
async def ingestion_status(
    service: DocumentIngestionUseCase = Depends(get_ingestion_service),
) -> IngestionStatusOut:
    """Retorna snapshot dos contadores do pipeline."""
    status = await service.get_status()
    return _status_to_out(status)


@router.post(
    "/reprocess", response_model=IngestionStatsOut, dependencies=[Depends(verify_admin_key)]
)
async def reprocess_errors(
    service: DocumentIngestionUseCase = Depends(get_ingestion_service),
) -> IngestionStatsOut:
    """Reprocessa documentos com status 'error'."""
    stats = await service.reprocess_errors()
    return _stats_to_out(stats)


@router.post(
    "/single/{document_url:path}", dependencies=[Depends(verify_admin_key)]
)
async def ingest_single(
    document_url: str,
    service: DocumentIngestionUseCase = Depends(get_ingestion_service),
) -> dict[str, object]:
    """Processa um único documento por URL."""
    success = await service.ingest_single(document_url)
    return {"ok": True, "success": success, "url": document_url}


# ── Health Check ──────────────────────────────────────────────────────────────


@router.post(
    "/health-check",
    response_model=HealthCheckReportOut,
    dependencies=[Depends(verify_admin_key)],
)
async def trigger_health_check(
    check_type: str = "all",
    concurrency: int = 5,
    service: DataHealthCheckUseCase = Depends(get_health_check_service),
) -> HealthCheckReportOut:
    """Executa health check. check_type: all | pages | documents | links."""
    if check_type == "pages":
        report = await service.check_pages(concurrency=concurrency)
    elif check_type == "documents":
        report = await service.check_documents(concurrency=concurrency)
    elif check_type == "links":
        report = await service.check_links(concurrency=concurrency)
    else:
        report = await service.check_all(concurrency=concurrency)
    return _report_to_out(report)


# ── Inconsistências ───────────────────────────────────────────────────────────


@router.get(
    "/inconsistencies/summary",
    response_model=InconsistencySummaryOut,
    dependencies=[Depends(verify_admin_key)],
)
async def inconsistency_summary(
    service: DataHealthCheckUseCase = Depends(get_health_check_service),
) -> InconsistencySummaryOut:
    """Retorna resumo por severidade, tipo e tipo de recurso."""
    # Busca todas as abertas sem paginação para agregar
    items = await service.get_inconsistencies(status="open", limit=10_000, offset=0)

    by_severity: dict[str, int] = defaultdict(int)
    by_type: dict[str, int] = defaultdict(int)
    by_resource_type: dict[str, int] = defaultdict(int)

    for inc in items:
        by_severity[inc.severity] += 1
        by_type[inc.inconsistency_type] += 1
        by_resource_type[inc.resource_type] += 1

    return InconsistencySummaryOut(
        total=len(items),
        by_severity=dict(by_severity),
        by_type=dict(by_type),
        by_resource_type=dict(by_resource_type),
    )


@router.get(
    "/inconsistencies",
    response_model=list[DataInconsistencyOut],
    dependencies=[Depends(verify_admin_key)],
)
async def list_inconsistencies(
    status: str = "open",
    resource_type: str | None = None,
    severity: str | None = None,
    limit: int = 50,
    offset: int = 0,
    service: DataHealthCheckUseCase = Depends(get_health_check_service),
) -> list[DataInconsistencyOut]:
    """Lista inconsistências filtradas por status, tipo de recurso e severidade."""
    items = await service.get_inconsistencies(
        status=status,
        resource_type=resource_type,
        severity=severity,
        limit=limit,
        offset=offset,
    )
    return [_inconsistency_to_out(i) for i in items]


@router.patch(
    "/inconsistencies/{id}/resolve",
    dependencies=[Depends(verify_admin_key)],
)
async def resolve_inconsistency(
    id: int,
    resolution_note: str,
    resolved_by: str,
    service: DataHealthCheckUseCase = Depends(get_health_check_service),
) -> dict[str, object]:
    """Marca inconsistência como resolvida."""
    await service.resolve_inconsistency(
        inconsistency_id=id,
        resolution_note=resolution_note,
        resolved_by=resolved_by,
    )
    return {"ok": True, "id": id}


@router.patch(
    "/inconsistencies/{id}/acknowledge",
    dependencies=[Depends(verify_admin_key)],
)
async def acknowledge_inconsistency(
    id: int,
    service: DataHealthCheckUseCase = Depends(get_health_check_service),
) -> dict[str, object]:
    """Marca inconsistência como reconhecida (em análise)."""
    await service.acknowledge_inconsistency(inconsistency_id=id)
    return {"ok": True, "id": id}


@router.patch(
    "/inconsistencies/{id}/ignore",
    dependencies=[Depends(verify_admin_key)],
)
async def ignore_inconsistency(
    id: int,
    reason: str,
    service: DataHealthCheckUseCase = Depends(get_health_check_service),
) -> dict[str, object]:
    """Marca inconsistência como ignorada com justificativa."""
    await service.ignore_inconsistency(inconsistency_id=id, reason=reason)
    return {"ok": True, "id": id}
