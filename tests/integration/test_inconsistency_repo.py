"""Testes de integração — InconsistencyRepository (Etapa 3 Pipeline V2).

TDD RED: escrito antes da implementação.

Critérios de aceite:
- save() persiste uma inconsistência e retorna o ID
- upsert() cria novo registro quando não existe
- upsert() atualiza registro aberto existente (mesmo URL + tipo) sem duplicar
- upsert() cria novo registro quando o existente está resolvido
- list_by_status() filtra por status, resource_type e severity
- list_by_status() respeita paginação (limit/offset)
- count_by_status() retorna contagem por status
- count_by_type() retorna contagem por tipo
- update_status() altera status de um registro pelo ID
- mark_resolved_by_url() resolve todos os registros de um recurso+tipo
- get_summary() retorna InconsistencySummary com totais e distribuições
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.adapters.outbound.postgres.connection import create_pool
from app.adapters.outbound.postgres.inconsistency_repo import PostgresInconsistencyRepository
from app.config.settings import Settings
from app.domain.value_objects.data_inconsistency import DataInconsistency
from app.domain.value_objects.inconsistency_summary import InconsistencySummary
from tests.integration.conftest import docker_required

PROJECT_ROOT = Path(__file__).parent.parent.parent


def _make_alembic_config(database_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def run_migrations(pg_settings: Settings) -> None:  # type: ignore[misc]
    cfg = _make_alembic_config(pg_settings.database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture
async def pool(pg_settings: Settings, run_migrations: None):  # type: ignore[misc]
    p = await create_pool(pg_settings)
    async with p.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE TABLE data_inconsistencies, query_logs, chat_messages, chat_sessions,
                document_tables, document_chunks, document_contents,
                page_links, navigation_tree, documents, pages
            RESTART IDENTITY CASCADE
            """
        )
    yield p
    await p.close()


@pytest.fixture
async def repo(pool):  # type: ignore[misc]
    return PostgresInconsistencyRepository(pool)


# ─── Helpers ──────────────────────────────────────────────────────────────────

URL_DOC = "https://www.tre-pi.jus.br/transparencia/relatorio.pdf"
URL_PAGE = "https://www.tre-pi.jus.br/transparencia"
URL_LINK = "https://www.tre-pi.jus.br/editais/2024"

_NOW = datetime(2026, 4, 11, 10, 0, 0, tzinfo=timezone.utc)


def _make_inconsistency(
    resource_url: str = URL_DOC,
    resource_type: str = "document",
    inconsistency_type: str = "document_not_found",
    severity: str = "critical",
    status: str = "open",
    detail: str = "HTTP 404 ao baixar documento",
    detected_by: str = "ingestion_pipeline",
    retry_count: int = 0,
) -> DataInconsistency:
    return DataInconsistency(
        id=None,
        resource_type=resource_type,
        severity=severity,
        inconsistency_type=inconsistency_type,
        resource_url=resource_url,
        resource_title="Relatório Anual",
        parent_page_url=URL_PAGE,
        detail=detail,
        http_status=404,
        error_message="Not Found",
        detected_at=_NOW,
        detected_by=detected_by,
        status=status,
        resolved_at=None,
        resolved_by=None,
        resolution_note=None,
        retry_count=retry_count,
        last_checked_at=_NOW,
    )


# ─── save ─────────────────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_save_cria_registro_e_retorna_id(
    repo: PostgresInconsistencyRepository,
) -> None:
    inc = _make_inconsistency()
    row_id = await repo.save(inc)
    assert isinstance(row_id, int)
    assert row_id > 0


@docker_required
@pytest.mark.integration
async def test_save_persiste_todos_os_campos(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    inc = _make_inconsistency(
        resource_url=URL_DOC,
        resource_type="document",
        inconsistency_type="document_not_found",
        severity="critical",
        detail="HTTP 404",
    )
    row_id = await repo.save(inc)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM data_inconsistencies WHERE id = $1", row_id
        )

    assert row["resource_url"] == URL_DOC
    assert row["resource_type"] == "document"
    assert row["inconsistency_type"] == "document_not_found"
    assert row["severity"] == "critical"
    assert row["detail"] == "HTTP 404"
    assert row["status"] == "open"
    assert row["retry_count"] == 0
    assert row["detected_by"] == "ingestion_pipeline"
    assert row["http_status"] == 404


@docker_required
@pytest.mark.integration
async def test_save_cria_multiplos_registros_independentes(
    repo: PostgresInconsistencyRepository,
) -> None:
    id1 = await repo.save(_make_inconsistency(resource_url=URL_DOC))
    id2 = await repo.save(_make_inconsistency(resource_url=URL_PAGE, resource_type="page"))
    assert id1 != id2


# ─── upsert ───────────────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_upsert_cria_registro_quando_nao_existe(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    inc = _make_inconsistency()
    row_id = await repo.upsert(URL_DOC, "document_not_found", inc)
    assert row_id > 0

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM data_inconsistencies WHERE resource_url = $1 AND inconsistency_type = $2",
            URL_DOC,
            "document_not_found",
        )
    assert count == 1


@docker_required
@pytest.mark.integration
async def test_upsert_atualiza_registro_aberto_existente(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    """Upsert no mesmo URL + tipo não deve criar duplicata enquanto status = 'open'."""
    inc1 = _make_inconsistency(detail="Primeira detecção", retry_count=0)
    id1 = await repo.upsert(URL_DOC, "document_not_found", inc1)

    inc2 = _make_inconsistency(detail="Segunda detecção", retry_count=1)
    id2 = await repo.upsert(URL_DOC, "document_not_found", inc2)

    assert id1 == id2  # Mesmo registro atualizado

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM data_inconsistencies WHERE id = $1", id1
        )
    assert row["retry_count"] == 1
    assert row["detail"] == "Segunda detecção"


@docker_required
@pytest.mark.integration
async def test_upsert_cria_novo_quando_existente_esta_resolvido(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    """Depois de resolver, uma nova detecção deve criar um novo registro."""
    inc = _make_inconsistency()
    id1 = await repo.upsert(URL_DOC, "document_not_found", inc)

    # Resolver o registro
    await repo.update_status(
        id1, "resolved", resolved_by="admin", resolution_note="URL corrigida"
    )

    # Nova detecção
    inc_novo = _make_inconsistency(detail="Novo problema detectado")
    id2 = await repo.upsert(URL_DOC, "document_not_found", inc_novo)

    assert id1 != id2  # Novo registro criado

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM data_inconsistencies WHERE resource_url = $1 AND inconsistency_type = $2",
            URL_DOC,
            "document_not_found",
        )
    assert count == 2


# ─── list_by_status ───────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_list_by_status_retorna_lista_vazia_sem_registros(
    repo: PostgresInconsistencyRepository,
) -> None:
    result = await repo.list_by_status("open")
    assert result == []


@docker_required
@pytest.mark.integration
async def test_list_by_status_filtra_por_status(
    repo: PostgresInconsistencyRepository,
) -> None:
    await repo.save(_make_inconsistency(resource_url=URL_DOC, status="open"))
    id2 = await repo.save(_make_inconsistency(resource_url=URL_PAGE, resource_type="page", status="open"))
    await repo.update_status(id2, "resolved", resolved_by="admin", resolution_note="ok")

    open_items = await repo.list_by_status("open")
    resolved_items = await repo.list_by_status("resolved")

    assert len(open_items) == 1
    assert open_items[0].resource_url == URL_DOC
    assert len(resolved_items) == 1
    assert resolved_items[0].resource_url == URL_PAGE


@docker_required
@pytest.mark.integration
async def test_list_by_status_filtra_por_resource_type(
    repo: PostgresInconsistencyRepository,
) -> None:
    await repo.save(_make_inconsistency(resource_url=URL_DOC, resource_type="document"))
    await repo.save(_make_inconsistency(resource_url=URL_PAGE, resource_type="page"))

    docs = await repo.list_by_status("open", resource_type="document")
    pages = await repo.list_by_status("open", resource_type="page")

    assert len(docs) == 1
    assert docs[0].resource_type == "document"
    assert len(pages) == 1
    assert pages[0].resource_type == "page"


@docker_required
@pytest.mark.integration
async def test_list_by_status_filtra_por_severity(
    repo: PostgresInconsistencyRepository,
) -> None:
    await repo.save(_make_inconsistency(resource_url=URL_DOC, severity="critical"))
    await repo.save(_make_inconsistency(resource_url=URL_PAGE, resource_type="page", severity="warning"))

    critical = await repo.list_by_status("open", severity="critical")
    warning = await repo.list_by_status("open", severity="warning")

    assert len(critical) == 1
    assert critical[0].severity == "critical"
    assert len(warning) == 1
    assert warning[0].severity == "warning"


@docker_required
@pytest.mark.integration
async def test_list_by_status_paginacao(
    repo: PostgresInconsistencyRepository,
) -> None:
    for i in range(5):
        await repo.save(
            _make_inconsistency(
                resource_url=f"https://www.tre-pi.jus.br/doc-{i}.pdf",
                detail=f"Erro {i}",
            )
        )

    page1 = await repo.list_by_status("open", limit=2, offset=0)
    page2 = await repo.list_by_status("open", limit=2, offset=2)
    page3 = await repo.list_by_status("open", limit=2, offset=4)

    assert len(page1) == 2
    assert len(page2) == 2
    assert len(page3) == 1

    urls_p1 = {i.resource_url for i in page1}
    urls_p2 = {i.resource_url for i in page2}
    assert urls_p1.isdisjoint(urls_p2)


@docker_required
@pytest.mark.integration
async def test_list_by_status_retorna_data_inconsistency(
    repo: PostgresInconsistencyRepository,
) -> None:
    await repo.save(_make_inconsistency())
    result = await repo.list_by_status("open")
    assert len(result) == 1
    assert isinstance(result[0], DataInconsistency)
    assert result[0].id is not None


# ─── count_by_status ──────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_count_by_status_retorna_zeros_sem_registros(
    repo: PostgresInconsistencyRepository,
) -> None:
    counts = await repo.count_by_status()
    assert isinstance(counts, dict)


@docker_required
@pytest.mark.integration
async def test_count_by_status_conta_corretamente(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    await repo.save(_make_inconsistency(resource_url=URL_DOC))
    id2 = await repo.save(_make_inconsistency(resource_url=URL_PAGE, resource_type="page"))
    await repo.update_status(id2, "resolved", resolved_by="admin", resolution_note="ok")
    await repo.save(_make_inconsistency(resource_url=URL_LINK, resource_type="link", severity="warning"))

    counts = await repo.count_by_status()
    assert counts.get("open", 0) == 2
    assert counts.get("resolved", 0) == 1


# ─── count_by_type ────────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_count_by_type_conta_por_tipo(
    repo: PostgresInconsistencyRepository,
) -> None:
    await repo.save(_make_inconsistency(inconsistency_type="document_not_found"))
    await repo.save(
        _make_inconsistency(
            resource_url=URL_PAGE,
            resource_type="page",
            inconsistency_type="page_unavailable",
        )
    )
    await repo.save(_make_inconsistency(resource_url=URL_LINK, inconsistency_type="document_not_found"))

    counts = await repo.count_by_type("open")
    assert counts.get("document_not_found", 0) == 2
    assert counts.get("page_unavailable", 0) == 1


# ─── update_status ────────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_update_status_altera_para_resolved(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    row_id = await repo.save(_make_inconsistency())

    await repo.update_status(
        row_id,
        "resolved",
        resolved_by="admin@tre-pi",
        resolution_note="URL corrigida",
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM data_inconsistencies WHERE id = $1", row_id
        )
    assert row["status"] == "resolved"
    assert row["resolved_by"] == "admin@tre-pi"
    assert row["resolution_note"] == "URL corrigida"
    assert row["resolved_at"] is not None


@docker_required
@pytest.mark.integration
async def test_update_status_altera_para_acknowledged(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    row_id = await repo.save(_make_inconsistency())

    await repo.update_status(row_id, "acknowledged")

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM data_inconsistencies WHERE id = $1", row_id
        )
    assert status == "acknowledged"


@docker_required
@pytest.mark.integration
async def test_update_status_altera_para_ignored(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    row_id = await repo.save(_make_inconsistency())

    await repo.update_status(row_id, "ignored")

    async with pool.acquire() as conn:
        status = await conn.fetchval(
            "SELECT status FROM data_inconsistencies WHERE id = $1", row_id
        )
    assert status == "ignored"


# ─── mark_resolved_by_url ─────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_mark_resolved_by_url_resolve_registros_abertos(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    # 2 registros abertos do mesmo URL + tipo
    await repo.save(_make_inconsistency())
    await repo.save(_make_inconsistency(detail="Segunda detecção"))

    count = await repo.mark_resolved_by_url(
        URL_DOC,
        "document_not_found",
        "Documento restaurado",
    )
    assert count == 2

    async with pool.acquire() as conn:
        open_count = await conn.fetchval(
            """
            SELECT COUNT(*) FROM data_inconsistencies
            WHERE resource_url = $1 AND inconsistency_type = $2 AND status = 'open'
            """,
            URL_DOC,
            "document_not_found",
        )
    assert open_count == 0


@docker_required
@pytest.mark.integration
async def test_mark_resolved_by_url_nao_afeta_outros_tipos(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    await repo.save(_make_inconsistency(inconsistency_type="document_not_found"))
    await repo.save(_make_inconsistency(inconsistency_type="encoding_error"))

    count = await repo.mark_resolved_by_url(
        URL_DOC, "document_not_found", "Corrigido"
    )
    assert count == 1

    async with pool.acquire() as conn:
        remaining = await conn.fetchval(
            """
            SELECT COUNT(*) FROM data_inconsistencies
            WHERE inconsistency_type = 'encoding_error' AND status = 'open'
            """
        )
    assert remaining == 1


@docker_required
@pytest.mark.integration
async def test_mark_resolved_by_url_retorna_zero_quando_nada_a_resolver(
    repo: PostgresInconsistencyRepository,
) -> None:
    count = await repo.mark_resolved_by_url(
        "https://www.tre-pi.jus.br/inexistente.pdf",
        "document_not_found",
        "Teste",
    )
    assert count == 0


# ─── get_summary ──────────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_get_summary_retorna_inconsistency_summary(
    repo: PostgresInconsistencyRepository,
) -> None:
    summary = await repo.get_summary()
    assert isinstance(summary, InconsistencySummary)


@docker_required
@pytest.mark.integration
async def test_get_summary_com_dados_variados(
    pool, repo: PostgresInconsistencyRepository
) -> None:
    # 2 open critical, 1 open warning, 1 resolved
    await repo.save(_make_inconsistency(resource_url=URL_DOC, severity="critical"))
    await repo.save(_make_inconsistency(resource_url=URL_PAGE, resource_type="page", severity="critical"))
    await repo.save(_make_inconsistency(resource_url=URL_LINK, resource_type="link", severity="warning"))
    id4 = await repo.save(
        _make_inconsistency(
            resource_url="https://www.tre-pi.jus.br/doc2.pdf",
            severity="warning",
        )
    )
    await repo.update_status(id4, "resolved", resolved_by="admin", resolution_note="ok")

    summary = await repo.get_summary()

    assert summary.total_open == 3
    assert summary.total_resolved == 1
    assert summary.by_severity.get("critical", 0) == 2
    assert summary.by_severity.get("warning", 0) == 1
    assert summary.by_resource_type.get("document", 0) >= 1
    assert summary.by_resource_type.get("page", 0) == 1
    assert summary.oldest_open is not None


@docker_required
@pytest.mark.integration
async def test_get_summary_banco_vazio(
    repo: PostgresInconsistencyRepository,
) -> None:
    summary = await repo.get_summary()
    assert summary.total_open == 0
    assert summary.total_resolved == 0
    assert summary.oldest_open is None
    assert summary.last_check is None
