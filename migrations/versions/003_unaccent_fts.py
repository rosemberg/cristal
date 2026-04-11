"""Migration 003 — FTS com unaccent (fix stemmer português para textos sem acento).

Problema: o stemmer 'portuguese' do PostgreSQL trata "diárias" e "DIARIAS"
como stems diferentes ('diár' vs 'diari'), causando falhas na busca full-text
de CSVs que armazenam dados em caixa alta sem acentuação.

Solução: criar configuração FTS customizada 'cristal_pt' que aplica 'unaccent'
antes do stemmer português, garantindo que "diárias" e "DIARIAS" produzam o
mesmo stem.

Alterações:
- Extension: unaccent
- Text search config: cristal_pt (portuguese + unaccent)
- Funções: pages_search_trigger() e chunks_search_trigger() → usar cristal_pt
- Reindexação: zera search_vector de pages e document_chunks (triggers recalculam)

Revision ID: c3d4e5f6a1b2
Revises: b2c3d4e5f6a1
Create Date: 2026-04-11
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ─── Alembic metadata ─────────────────────────────────────────────────────────

revision: str = "c3d4e5f6a1b2"
down_revision: str | None = "b2c3d4e5f6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    """Cria config FTS cristal_pt com unaccent e reindexar todos os vetores."""

    # ── Extension: unaccent ───────────────────────────────────────────────────
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS unaccent"))

    # ── Text Search Configuration customizada ────────────────────────────────
    # Copia a config 'portuguese' e acrescenta 'unaccent' antes do stemmer
    # para que "DIARIAS" e "diárias" produzam o mesmo lexema.
    op.execute(
        sa.text(
            "CREATE TEXT SEARCH CONFIGURATION cristal_pt (COPY = portuguese)"
        )
    )
    op.execute(
        sa.text(
            """
            ALTER TEXT SEARCH CONFIGURATION cristal_pt
                ALTER MAPPING FOR hword, hword_part, word
                WITH unaccent, portuguese_stem
            """
        )
    )

    # ── Atualiza trigger pages_search_trigger() ───────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION pages_search_trigger()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    setweight(to_tsvector('cristal_pt', COALESCE(NEW.title, '')), 'A') ||
                    setweight(to_tsvector('cristal_pt', COALESCE(NEW.description, '')), 'B') ||
                    setweight(to_tsvector('cristal_pt', COALESCE(NEW.category, '')), 'B') ||
                    setweight(to_tsvector('cristal_pt', COALESCE(NEW.main_content, '')), 'C') ||
                    setweight(
                        to_tsvector('cristal_pt', COALESCE(array_to_string(NEW.tags, ' '), '')),
                        'B'
                    );
                NEW.updated_at := NOW();
                RETURN NEW;
            END
            $$ LANGUAGE plpgsql
            """
        )
    )

    # ── Atualiza trigger chunks_search_trigger() ──────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION chunks_search_trigger()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    setweight(
                        to_tsvector('cristal_pt', COALESCE(NEW.section_title, '')), 'A'
                    ) ||
                    setweight(
                        to_tsvector('cristal_pt', COALESCE(NEW.chunk_text, '')), 'B'
                    );
                RETURN NEW;
            END
            $$ LANGUAGE plpgsql
            """
        )
    )

    # ── Reindexação: zera search_vector — triggers recalculam com cristal_pt ──
    # UPDATE dispara o trigger BEFORE UPDATE, que recalcula o search_vector.
    op.execute(sa.text("UPDATE pages SET search_vector = NULL"))
    op.execute(sa.text("UPDATE document_chunks SET search_vector = NULL"))


# ─── Downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    """Reverte para configuração FTS 'portuguese' original."""

    # Restaura triggers com 'portuguese'
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION pages_search_trigger()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    setweight(to_tsvector('portuguese', COALESCE(NEW.title, '')), 'A') ||
                    setweight(to_tsvector('portuguese', COALESCE(NEW.description, '')), 'B') ||
                    setweight(to_tsvector('portuguese', COALESCE(NEW.category, '')), 'B') ||
                    setweight(to_tsvector('portuguese', COALESCE(NEW.main_content, '')), 'C') ||
                    setweight(
                        to_tsvector('portuguese', COALESCE(array_to_string(NEW.tags, ' '), '')),
                        'B'
                    );
                NEW.updated_at := NOW();
                RETURN NEW;
            END
            $$ LANGUAGE plpgsql
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE FUNCTION chunks_search_trigger()
            RETURNS trigger AS $$
            BEGIN
                NEW.search_vector :=
                    setweight(
                        to_tsvector('portuguese', COALESCE(NEW.section_title, '')), 'A'
                    ) ||
                    setweight(
                        to_tsvector('portuguese', COALESCE(NEW.chunk_text, '')), 'B'
                    );
                RETURN NEW;
            END
            $$ LANGUAGE plpgsql
            """
        )
    )

    # Reindexação de volta para 'portuguese'
    op.execute(sa.text("UPDATE pages SET search_vector = NULL"))
    op.execute(sa.text("UPDATE document_chunks SET search_vector = NULL"))

    # Remove configuração customizada
    op.execute(sa.text("DROP TEXT SEARCH CONFIGURATION IF EXISTS cristal_pt"))
    op.execute(sa.text("DROP EXTENSION IF EXISTS unaccent"))
