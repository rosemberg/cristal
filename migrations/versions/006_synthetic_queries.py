"""Migration 006 — tabela synthetic_queries para Query Augmentation (Fase 1 NOVO_RAG).

Problema: embeddings de chunks burocráticos estão semanticamente distantes
das perguntas naturais do cidadão, reduzindo o recall da busca semântica.

Solução: tabela synthetic_queries armazena perguntas geradas por LLM para
cada chunk. Os embeddings dessas perguntas ficam em `embeddings` com
source_type='synthetic_query', ampliando as estratégias de busca do RRF.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a1b2c3d4
Create Date: 2026-04-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ─── Alembic metadata ─────────────────────────────────────────────────────────

revision: str = "f6a7b8c9d0e1"
down_revision: str | None = "e5f6a1b2c3d4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── 1. Tabela synthetic_queries ───────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE synthetic_queries (
                id          SERIAL PRIMARY KEY,
                source_type TEXT NOT NULL,
                source_id   INTEGER NOT NULL,
                question    TEXT NOT NULL,
                model_used  TEXT NOT NULL,
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )

    op.execute(
        sa.text(
            "CREATE INDEX idx_sq_source ON synthetic_queries(source_type, source_id)"
        )
    )

    # ── 2. Atualiza CHECK constraint de embeddings para incluir 'synthetic_query' ─
    op.execute(
        sa.text(
            "ALTER TABLE embeddings DROP CONSTRAINT IF EXISTS embeddings_source_type_check"
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE embeddings
            ADD CONSTRAINT embeddings_source_type_check
            CHECK (source_type IN ('page', 'chunk', 'table', 'page_chunk', 'synthetic_query'))
            """
        )
    )

    # ── 3. HNSW index para synthetic_query embeddings ─────────────────────────
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_embeddings_synthetic_query
                ON embeddings
                USING hnsw (embedding vector_cosine_ops)
                WHERE source_type = 'synthetic_query'
            """
        )
    )


# ─── Downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_embeddings_synthetic_query"))

    # Restaura CHECK constraint sem 'synthetic_query'
    op.execute(
        sa.text(
            "ALTER TABLE embeddings DROP CONSTRAINT IF EXISTS embeddings_source_type_check"
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TABLE embeddings
            ADD CONSTRAINT embeddings_source_type_check
            CHECK (source_type IN ('page', 'chunk', 'table', 'page_chunk'))
            """
        )
    )

    op.execute(sa.text("DROP INDEX IF EXISTS idx_sq_source"))
    op.execute(sa.text("DROP TABLE IF EXISTS synthetic_queries"))
