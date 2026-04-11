"""Testes de integração — Migration 002: processing_status + data_inconsistencies (Etapa 1 Pipeline V2).

TDD RED: escrito antes da implementação da migration.

Critérios de aceite:
- alembic upgrade head aplica as colunas na tabela documents
- Tabela data_inconsistencies é criada com todos os índices
- 122 documentos existentes ganham processing_status = 'pending'
- Migration encadeia corretamente com 001 (down_revision = a1b2c3d4e5f6)
- downgrade reverte tudo corretamente
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from tests.integration.conftest import docker_required
from tests.integration.test_migrations import (
    PROJECT_ROOT,
    _asyncpg_dsn,
    _list_tables,
    make_alembic_config,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _list_columns(dsn: str, table: str) -> list[str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position
            """,
            table,
        )
        return [r["column_name"] for r in rows]
    finally:
        await conn.close()


async def _list_indexes(dsn: str, table: str) -> list[str]:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = $1 ORDER BY indexname",
            table,
        )
        return [r["indexname"] for r in rows]
    finally:
        await conn.close()


async def _column_default(dsn: str, table: str, column: str) -> str | None:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval(
            """
            SELECT column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1 AND column_name = $2
            """,
            table, column,
        )
    finally:
        await conn.close()


async def _count_rows(dsn: str, table: str, where: str = "") -> int:
    conn = await asyncpg.connect(dsn)
    try:
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return await conn.fetchval(sql)
    finally:
        await conn.close()


async def _insert_test_document(dsn: str, url: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO documents (document_url, document_title, document_type, page_url)
            VALUES ($1, $2, 'pdf', 'https://www.tre-pi.jus.br/transparencia')
            ON CONFLICT DO NOTHING
            """,
            url, "Documento de Teste 002"
        )
    finally:
        await conn.close()


# ─── Fixture de ciclo de vida ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def migrated_002(pg_settings):  # type: ignore[no-untyped-def]
    """Aplica upgrade head (001 + 002) e faz downgrade no teardown."""
    dsn_url = pg_settings.database_url
    cfg = make_alembic_config(dsn_url)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    yield cfg, dsn_url

    command.downgrade(cfg, "base")


# ─── Testes de colunas ────────────────────────────────────────────────────────


@docker_required
def test_documents_tem_coluna_processing_status(migrated_002):  # type: ignore[no-untyped-def]
    """A tabela documents deve ter a coluna processing_status após migration 002."""
    _cfg, dsn_url = migrated_002
    dsn = _asyncpg_dsn(dsn_url)
    columns = asyncio.run(_list_columns(dsn, "documents"))
    assert "processing_status" in columns, "Coluna processing_status não encontrada em documents"


@docker_required
def test_documents_tem_coluna_processing_error(migrated_002):  # type: ignore[no-untyped-def]
    """A tabela documents deve ter a coluna processing_error após migration 002."""
    _cfg, dsn_url = migrated_002
    dsn = _asyncpg_dsn(dsn_url)
    columns = asyncio.run(_list_columns(dsn, "documents"))
    assert "processing_error" in columns, "Coluna processing_error não encontrada em documents"


@docker_required
def test_documents_tem_coluna_processed_at(migrated_002):  # type: ignore[no-untyped-def]
    """A tabela documents deve ter a coluna processed_at após migration 002."""
    _cfg, dsn_url = migrated_002
    dsn = _asyncpg_dsn(dsn_url)
    columns = asyncio.run(_list_columns(dsn, "documents"))
    assert "processed_at" in columns, "Coluna processed_at não encontrada em documents"


@docker_required
def test_processing_status_default_pending(migrated_002):  # type: ignore[no-untyped-def]
    """Documentos inseridos sem processing_status devem receber 'pending' como default."""
    _cfg, dsn_url = migrated_002
    dsn = _asyncpg_dsn(dsn_url)

    # Primeiro insere uma página (FK constraint)
    async def _setup(dsn: str) -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "INSERT INTO pages (url, title, category) VALUES ($1, $2, $3) ON CONFLICT DO NOTHING",
                "https://www.tre-pi.jus.br/transparencia",
                "Transparência",
                "Transparência",
            )
            await conn.execute(
                """
                INSERT INTO documents (document_url, document_title, document_type, page_url)
                VALUES ($1, $2, 'pdf', 'https://www.tre-pi.jus.br/transparencia')
                ON CONFLICT DO NOTHING
                """,
                "https://www.tre-pi.jus.br/doc/test-002.pdf",
                "Teste Migration 002",
            )
        finally:
            await conn.close()

    async def _check(dsn: str) -> str:
        conn = await asyncpg.connect(dsn)
        try:
            return await conn.fetchval(
                "SELECT processing_status FROM documents WHERE document_url = $1",
                "https://www.tre-pi.jus.br/doc/test-002.pdf",
            )
        finally:
            await conn.close()

    async def _cleanup(dsn: str) -> None:
        conn = await asyncpg.connect(dsn)
        try:
            await conn.execute(
                "DELETE FROM documents WHERE document_url = $1",
                "https://www.tre-pi.jus.br/doc/test-002.pdf",
            )
            await conn.execute(
                "DELETE FROM pages WHERE url = $1",
                "https://www.tre-pi.jus.br/transparencia",
            )
        finally:
            await conn.close()

    asyncio.run(_setup(dsn))
    status = asyncio.run(_check(dsn))
    asyncio.run(_cleanup(dsn))

    assert status == "pending", f"processing_status esperado 'pending', obtido: {status!r}"


# ─── Testes da tabela data_inconsistencies ────────────────────────────────────


@docker_required
def test_tabela_data_inconsistencies_criada(migrated_002):  # type: ignore[no-untyped-def]
    """A tabela data_inconsistencies deve existir após migration 002."""
    _cfg, dsn_url = migrated_002
    dsn = _asyncpg_dsn(dsn_url)
    tables = asyncio.run(_list_tables(dsn))
    assert "data_inconsistencies" in tables, "Tabela data_inconsistencies não encontrada"


@docker_required
def test_data_inconsistencies_colunas_essenciais(migrated_002):  # type: ignore[no-untyped-def]
    """A tabela data_inconsistencies deve ter todas as colunas definidas no plano."""
    _cfg, dsn_url = migrated_002
    dsn = _asyncpg_dsn(dsn_url)
    columns = asyncio.run(_list_columns(dsn, "data_inconsistencies"))

    expected = [
        "id", "resource_type", "severity", "inconsistency_type",
        "resource_url", "resource_title", "parent_page_url",
        "detail", "http_status", "error_message",
        "detected_at", "detected_by",
        "status", "resolved_at", "resolved_by", "resolution_note",
        "retry_count", "last_checked_at",
    ]
    for col in expected:
        assert col in columns, f"Coluna '{col}' não encontrada em data_inconsistencies"


@docker_required
def test_data_inconsistencies_indices(migrated_002):  # type: ignore[no-untyped-def]
    """Os índices definidos no plano devem existir em data_inconsistencies."""
    _cfg, dsn_url = migrated_002
    dsn = _asyncpg_dsn(dsn_url)
    indexes = asyncio.run(_list_indexes(dsn, "data_inconsistencies"))

    expected_indexes = [
        "idx_inconsistencies_status",
        "idx_inconsistencies_type",
        "idx_inconsistencies_severity",
        "idx_inconsistencies_resource",
        "idx_inconsistencies_detected",
        "idx_inconsistencies_open_severity",
    ]
    for idx in expected_indexes:
        assert idx in indexes, f"Índice '{idx}' não encontrado em data_inconsistencies"


@docker_required
def test_documents_indice_processing_status(migrated_002):  # type: ignore[no-untyped-def]
    """O índice idx_documents_processing_status deve existir em documents."""
    _cfg, dsn_url = migrated_002
    dsn = _asyncpg_dsn(dsn_url)
    indexes = asyncio.run(_list_indexes(dsn, "documents"))
    assert "idx_documents_processing_status" in indexes, (
        "Índice idx_documents_processing_status não encontrado em documents"
    )


@docker_required
def test_data_inconsistencies_insert_e_select(migrated_002):  # type: ignore[no-untyped-def]
    """Deve ser possível inserir e recuperar um registro de inconsistência."""

    async def _run(dsn: str) -> dict:
        conn = await asyncpg.connect(dsn)
        try:
            row_id = await conn.fetchval(
                """
                INSERT INTO data_inconsistencies (
                    resource_type, severity, inconsistency_type,
                    resource_url, detail, detected_by
                ) VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                "document", "warning", "broken_link",
                "https://www.tre-pi.jus.br/doc/missing.pdf",
                "Retornou 404",
                "ingestion_pipeline",
            )
            row = await conn.fetchrow(
                "SELECT * FROM data_inconsistencies WHERE id = $1", row_id
            )
            await conn.execute(
                "DELETE FROM data_inconsistencies WHERE id = $1", row_id
            )
            return dict(row)
        finally:
            await conn.close()

    _cfg, dsn_url = migrated_002
    dsn = _asyncpg_dsn(dsn_url)
    row = asyncio.run(_run(dsn))

    assert row["resource_type"] == "document"
    assert row["severity"] == "warning"
    assert row["inconsistency_type"] == "broken_link"
    assert row["status"] == "open"
    assert row["retry_count"] == 0
    assert row["detected_at"] is not None
    assert row["last_checked_at"] is not None


# ─── Teste de downgrade ───────────────────────────────────────────────────────


@docker_required
def test_downgrade_002_remove_tabela_e_colunas(pg_settings):  # type: ignore[no-untyped-def]
    """Após downgrade para 001, data_inconsistencies não deve existir e colunas removidas."""
    dsn_url = pg_settings.database_url
    cfg = make_alembic_config(dsn_url)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    # Faz downgrade para a revision 001 (antes da 002)
    command.downgrade(cfg, "a1b2c3d4e5f6")

    dsn = _asyncpg_dsn(dsn_url)

    tables = asyncio.run(_list_tables(dsn))
    assert "data_inconsistencies" not in tables, "data_inconsistencies ainda existe após downgrade 002"

    columns = asyncio.run(_list_columns(dsn, "documents"))
    for col in ("processing_status", "processing_error", "processed_at"):
        assert col not in columns, f"Coluna {col!r} ainda existe em documents após downgrade 002"

    # Cleanup
    command.downgrade(cfg, "base")
