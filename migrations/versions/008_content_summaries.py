"""Migration 008 — Sumarização e Indexação Multinível (Fase 2 NOVO_RAG).

Cria a tabela `section_summaries` para sumários de seções de documentos
longos (>10 páginas) e atualiza o CHECK constraint de `embeddings` para
incluir os novos source_types 'page_summary' e 'section_summary'.

Estratégia:
- page_summary: embedding do campo content_summary de cada página (gerado
  por LLM, substituindo o truncamento do crawler)
- section_summary: embedding de seções lógicas de documentos longos

Revision ID: b2c3d4e5f6a7
Revises: 007fix001src
Create Date: 2026-04-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ─── Alembic metadata ─────────────────────────────────────────────────────────

revision: str = "b2c3d4e5f6a7"
down_revision: str | None = "007fix001src"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── 1. Tabela section_summaries ───────────────────────────────────────────
    # Armazena sumários de seções lógicas de documentos longos (>10 páginas).
    # source_id em embeddings aponta para section_summaries.id.
    op.execute(
        sa.text(
            """
            CREATE TABLE section_summaries (
                id              SERIAL PRIMARY KEY,
                document_id     INTEGER NOT NULL,
                section_title   TEXT,
                page_range      TEXT,
                summary_text    TEXT NOT NULL,
                model_used      TEXT NOT NULL,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )

    op.execute(
        sa.text(
            "CREATE INDEX idx_ss_document ON section_summaries(document_id)"
        )
    )

    # ── 2. Atualiza CHECK constraint de embeddings ────────────────────────────
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
            CHECK (source_type IN (
                'page', 'chunk', 'table', 'page_chunk',
                'synthetic_query', 'page_summary', 'section_summary'
            ))
            """
        )
    )

    # ── 3. HNSW indexes para novos source_types ───────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_embeddings_page_summary
                ON embeddings
                USING hnsw (embedding vector_cosine_ops)
                WHERE source_type = 'page_summary'
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_embeddings_section_summary
                ON embeddings
                USING hnsw (embedding vector_cosine_ops)
                WHERE source_type = 'section_summary'
            """
        )
    )


# ─── Downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_embeddings_section_summary"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_embeddings_page_summary"))

    # Restaura CHECK constraint sem os novos source_types
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
            CHECK (source_type IN (
                'page', 'chunk', 'table', 'page_chunk', 'synthetic_query'
            ))
            """
        )
    )

    op.execute(sa.text("DROP INDEX IF EXISTS idx_ss_document"))
    op.execute(sa.text("DROP TABLE IF EXISTS section_summaries"))
