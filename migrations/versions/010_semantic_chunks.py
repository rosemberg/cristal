"""Migration 010 — Chunking Semântico (Fase 4 NOVO_RAG).

Adiciona colunas de versionamento e metadados estruturais às tabelas de chunks
para suportar o novo SemanticChunker (version=2) em paralelo ao TextChunker
legado (version=1).

Campos adicionados:
- document_chunks.version       INT DEFAULT 1  — 1=TextChunker, 2=SemanticChunker
- document_chunks.has_table     BOOL DEFAULT false — chunk contém tabela inline
- document_chunks.parent_chunk_id INT NULL     — ID do chunk pai (para sub-chunks)
- page_chunks.version           INT DEFAULT 1

Revision ID: d4e5f7a8b9c0
Revises: c3d4e5f6a7b8
Create Date: 2026-04-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ─── Alembic metadata ─────────────────────────────────────────────────────────

revision: str = "d4e5f7a8b9c0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── 1. document_chunks: version, has_table, parent_chunk_id ──────────────
    op.execute(
        sa.text(
            "ALTER TABLE document_chunks ADD COLUMN version INT NOT NULL DEFAULT 1"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE document_chunks ADD COLUMN has_table BOOLEAN NOT NULL DEFAULT false"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE document_chunks ADD COLUMN parent_chunk_id INT"
        )
    )

    op.execute(
        sa.text(
            "CREATE INDEX idx_dc_version ON document_chunks(version)"
        )
    )

    # ── 2. page_chunks: version ───────────────────────────────────────────────
    op.execute(
        sa.text(
            "ALTER TABLE page_chunks ADD COLUMN version INT NOT NULL DEFAULT 1"
        )
    )

    op.execute(
        sa.text(
            "CREATE INDEX idx_pc_version ON page_chunks(version)"
        )
    )


# ─── Downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_pc_version"))
    op.execute(sa.text("ALTER TABLE page_chunks DROP COLUMN IF EXISTS version"))

    op.execute(sa.text("DROP INDEX IF EXISTS idx_dc_version"))
    op.execute(sa.text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS parent_chunk_id"))
    op.execute(sa.text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS has_table"))
    op.execute(sa.text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS version"))
