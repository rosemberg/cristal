"""Migration 002 — Controle de processamento de documentos e tabela de inconsistências.

Alterações:
- Tabela documents: + processing_status, processing_error, processed_at
- Nova tabela: data_inconsistencies (registro centralizado de problemas)
- Índices: idx_documents_processing_status e 6 índices em data_inconsistencies

Revision ID: b2c3d4e5f6a1
Revises: a1b2c3d4e5f6
Create Date: 2026-04-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ─── Alembic metadata ─────────────────────────────────────────────────────────

revision: str = "b2c3d4e5f6a1"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    """Adiciona controle de processamento e tabela de inconsistências."""

    # ── Colunas em documents ──────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            ALTER TABLE documents
                ADD COLUMN processing_status VARCHAR(20) NOT NULL DEFAULT 'pending',
                ADD COLUMN processing_error  TEXT,
                ADD COLUMN processed_at      TIMESTAMPTZ
            """
        )
    )

    op.execute(
        sa.text(
            "CREATE INDEX idx_documents_processing_status ON documents(processing_status)"
        )
    )

    # ── Tabela: data_inconsistencies ──────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE data_inconsistencies (
                id                  SERIAL PRIMARY KEY,

                -- Classificação
                resource_type       VARCHAR(20) NOT NULL,
                severity            VARCHAR(10) NOT NULL DEFAULT 'warning',
                inconsistency_type  VARCHAR(50) NOT NULL,

                -- Recurso afetado
                resource_url        TEXT NOT NULL,
                resource_title      TEXT,
                parent_page_url     TEXT,

                -- Detalhes
                detail              TEXT NOT NULL,
                http_status         INTEGER,
                error_message       TEXT,

                -- Rastreamento
                detected_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                detected_by         VARCHAR(50) NOT NULL,

                -- Resolução
                status              VARCHAR(20) NOT NULL DEFAULT 'open',
                resolved_at         TIMESTAMPTZ,
                resolved_by         VARCHAR(100),
                resolution_note     TEXT,

                -- Controle
                retry_count         INTEGER NOT NULL DEFAULT 0,
                last_checked_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )

    # Índices para consultas do admin
    op.execute(
        sa.text("CREATE INDEX idx_inconsistencies_status ON data_inconsistencies(status)")
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_inconsistencies_type ON data_inconsistencies(inconsistency_type)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_inconsistencies_severity ON data_inconsistencies(severity)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_inconsistencies_resource ON data_inconsistencies(resource_url)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_inconsistencies_detected ON data_inconsistencies(detected_at DESC)"
        )
    )

    # Índice composto para consulta principal do dashboard (apenas registros abertos)
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_inconsistencies_open_severity
                ON data_inconsistencies(status, severity, detected_at DESC)
                WHERE status = 'open'
            """
        )
    )


# ─── Downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    """Remove tabela de inconsistências e colunas de processamento."""

    op.execute(sa.text("DROP TABLE IF EXISTS data_inconsistencies"))

    op.execute(
        sa.text("DROP INDEX IF EXISTS idx_documents_processing_status")
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE documents
                DROP COLUMN IF EXISTS processing_status,
                DROP COLUMN IF EXISTS processing_error,
                DROP COLUMN IF EXISTS processed_at
            """
        )
    )
