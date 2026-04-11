"""Migration 004 — pgvector + tabela embeddings separada.

Cria a extensão `vector` (pgvector) e a tabela `embeddings`, que armazena
vetores semânticos de qualquer fonte (page, chunk ou table) de forma desacoplada
das tabelas principais.

Vantagens da tabela separada:
- Trocar modelo de embedding sem alterar as tabelas `pages` / `document_chunks`
- Backfill sem lock nas tabelas principais
- Suporte a múltiplos modelos em paralelo (A/B testing)
- `source_text_hash` detecta chunks que mudaram e precisam re-embedding

Revision ID: d4e5f6a1b2c3
Revises: c3d4e5f6a1b2
Create Date: 2026-04-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ─── Alembic metadata ─────────────────────────────────────────────────────────

revision: str = "d4e5f6a1b2c3"
down_revision: str | None = "c3d4e5f6a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    """Cria extensão vector e tabela embeddings com indexes HNSW."""

    # ── Extension: pgvector ───────────────────────────────────────────────────
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))

    # ── Tabela embeddings ─────────────────────────────────────────────────────
    # source_type: 'page' | 'chunk' | 'table'
    # source_id:   FK lógica (sem FK física para evitar lock e permitir deleção)
    # source_text_hash: SHA-256 do texto original — detecta mudanças sem re-fetch
    # model_name:  versionamento do modelo de embedding
    # dimensions:  768 para text-embedding-005
    op.execute(
        sa.text(
            """
            CREATE TABLE embeddings (
                id                SERIAL PRIMARY KEY,
                source_type       VARCHAR(10)  NOT NULL
                                    CHECK (source_type IN ('page', 'chunk', 'table')),
                source_id         INTEGER      NOT NULL,
                source_text_hash  VARCHAR(64),
                model_name        VARCHAR(100) NOT NULL
                                    DEFAULT 'text-embedding-005',
                dimensions        SMALLINT     NOT NULL DEFAULT 768,
                embedding         vector(768)  NOT NULL,
                created_at        TIMESTAMPTZ  DEFAULT NOW(),
                UNIQUE (source_type, source_id, model_name)
            )
            """
        )
    )

    # ── Indexes HNSW por tipo de fonte ────────────────────────────────────────
    # Índices parciais por source_type: menor footprint, busca mais eficiente.
    # vector_cosine_ops → distância cosseno (1 - cosine_similarity).
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_embeddings_chunk
                ON embeddings
                USING hnsw (embedding vector_cosine_ops)
                WHERE source_type = 'chunk'
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_embeddings_page
                ON embeddings
                USING hnsw (embedding vector_cosine_ops)
                WHERE source_type = 'page'
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_embeddings_table
                ON embeddings
                USING hnsw (embedding vector_cosine_ops)
                WHERE source_type = 'table'
            """
        )
    )

    # ── Index para lookup por source (backfill / upsert) ──────────────────────
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_embeddings_source
                ON embeddings (source_type, source_id)
            """
        )
    )


# ─── Downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    """Remove tabela embeddings e extensão vector."""

    op.execute(sa.text("DROP TABLE IF EXISTS embeddings"))
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector"))
