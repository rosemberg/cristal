"""Migration 009 — Enriquecimento de Metadados Estruturados (Fase 3 NOVO_RAG).

Cria tabelas `page_entities` e `page_tags` para armazenar entidades extraídas
por regex (NER) e tags temáticas geradas por LLM para cada página.

Estratégia:
- page_entities: entidades estruturadas (datas, valores, contratos, CNPJs, etc.)
  extraídas por regex (Etapa A) e opcionalmente complementadas pelo LLM
- page_tags: classificação temática (licitacao, contrato, diaria, etc.)
  gerada por LLM em batch (Etapa B)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-04-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ─── Alembic metadata ─────────────────────────────────────────────────────────

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "b2c3d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── 1. Tabela page_entities ───────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE page_entities (
                id            SERIAL PRIMARY KEY,
                page_id       INTEGER NOT NULL,
                entity_type   TEXT NOT NULL,
                entity_value  TEXT NOT NULL,
                raw_text      TEXT,
                confidence    REAL NOT NULL DEFAULT 1.0,
                created_at    TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )

    op.execute(
        sa.text(
            "CREATE INDEX idx_pe_page ON page_entities(page_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_pe_type_value ON page_entities(entity_type, entity_value)"
        )
    )

    # ── 2. Tabela page_tags ───────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE page_tags (
                id         SERIAL PRIMARY KEY,
                page_id    INTEGER NOT NULL,
                tag        TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0,
                UNIQUE(page_id, tag)
            )
            """
        )
    )

    op.execute(
        sa.text(
            "CREATE INDEX idx_pt_tag ON page_tags(tag)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_pt_page ON page_tags(page_id)"
        )
    )


# ─── Downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_pt_page"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_pt_tag"))
    op.execute(sa.text("DROP TABLE IF EXISTS page_tags"))

    op.execute(sa.text("DROP INDEX IF EXISTS idx_pe_type_value"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_pe_page"))
    op.execute(sa.text("DROP TABLE IF EXISTS page_entities"))
