"""Migration 005 — tabela page_chunks para chunks de main_content de páginas.

Problema: páginas longas (ex: lista de contratos, servidores) têm um único
embedding para centenas de kilobytes. O RAG não encontra dados que estão além
dos primeiros tokens usados para gerar o embedding.

Solução: dividir main_content em chunks menores, indexá-los em FTS e gerar
embeddings individuais (source_type='page_chunk').

Revision ID: e5f6a1b2c3d4
Revises: d4e5f6a1b2c3
Create Date: 2026-04-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ─── Alembic metadata ─────────────────────────────────────────────────────────

revision: str = "e5f6a1b2c3d4"
down_revision: str | None = "d4e5f6a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    # ── 1. Tabela page_chunks ─────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE page_chunks (
                id            SERIAL PRIMARY KEY,
                page_id       INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
                page_url      TEXT NOT NULL,
                chunk_index   INTEGER NOT NULL,
                chunk_text    TEXT NOT NULL,
                token_count   INTEGER,
                search_vector TSVECTOR,
                created_at    TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE (page_url, chunk_index)
            )
            """
        )
    )

    # ── 2. Indexes ────────────────────────────────────────────────────────────
    op.execute(sa.text("CREATE INDEX idx_page_chunks_page ON page_chunks(page_id)"))
    op.execute(sa.text("CREATE INDEX idx_page_chunks_url  ON page_chunks(page_url)"))
    op.execute(
        sa.text(
            "CREATE INDEX idx_page_chunks_search ON page_chunks USING GIN(search_vector)"
        )
    )

    # ── 3. FTS trigger ────────────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION page_chunks_search_trigger() RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    to_tsvector('cristal_pt', NEW.chunk_text);
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE TRIGGER page_chunks_search_update
            BEFORE INSERT OR UPDATE ON page_chunks
            FOR EACH ROW EXECUTE FUNCTION page_chunks_search_trigger()
            """
        )
    )

    # ── 4. Atualiza CHECK constraint de embeddings para incluir 'page_chunk' ──
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

    # ── 5. HNSW index para page_chunk embeddings ──────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE INDEX idx_embeddings_page_chunk
                ON embeddings
                USING hnsw (embedding vector_cosine_ops)
                WHERE source_type = 'page_chunk'
            """
        )
    )


# ─── Downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_embeddings_page_chunk"))

    # Restaura CHECK constraint original
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
            CHECK (source_type IN ('page', 'chunk', 'table'))
            """
        )
    )

    op.execute(
        sa.text(
            "DROP TRIGGER IF EXISTS page_chunks_search_update ON page_chunks"
        )
    )
    op.execute(sa.text("DROP FUNCTION IF EXISTS page_chunks_search_trigger()"))
    op.execute(sa.text("DROP TABLE IF EXISTS page_chunks"))
