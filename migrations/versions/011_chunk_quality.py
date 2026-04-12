"""Migration 011 — Detecção e Correção de Dados Corrompidos (Fase 5 NOVO_RAG).

Adiciona colunas de qualidade a document_chunks e page_chunks para suportar
o pipeline de scoring, quarentena e deduplicação.

Campos adicionados:
- quality_score     REAL        — 0.0-1.0; NULL = ainda não calculado
- quality_flags     TEXT[]      — flags de problema: ['ocr_artifacts', ...]
- quarantined       BOOLEAN     — true = excluído da busca por baixa qualidade

Revision ID: e5f6a8b9c0d1
Revises: d4e5f7a8b9c0
Create Date: 2026-04-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a8b9c0d1"
down_revision: str | None = "d4e5f7a8b9c0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("document_chunks", "page_chunks"):
        op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN quality_score REAL"))
        op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN quality_flags TEXT[] DEFAULT '{{}}'"))
        op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN quarantined BOOLEAN NOT NULL DEFAULT false"))
        op.execute(sa.text(
            f"CREATE INDEX idx_{table[:2]}c_quarantine ON {table}(quarantined) WHERE quarantined = true"
        ))


def downgrade() -> None:
    for table in ("document_chunks", "page_chunks"):
        short = table[:2]
        op.execute(sa.text(f"DROP INDEX IF EXISTS idx_{short}c_quarantine"))
        op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS quarantined"))
        op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS quality_flags"))
        op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN IF EXISTS quality_score"))
