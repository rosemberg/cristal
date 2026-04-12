"""Migration 007 — alarga embeddings.source_type de VARCHAR(10) para TEXT.

A migration 004 criou source_type como VARCHAR(10), suficiente para
'page', 'chunk' e 'table'. As migrations 005 e 006 adicionaram
'page_chunk' (10 chars) e 'synthetic_query' (15 chars) no CHECK
constraint, mas não alargaram a coluna. Esta migration corrige isso.

Revision ID: a1b2c3d4e5f6
Revises: f6a7b8c9d0e1
Create Date: 2026-04-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007fix001src"
down_revision: str | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Remove o CHECK inline (já gerenciado pela constraint separada da 006)
    # e alarga a coluna para TEXT.
    op.execute(
        sa.text(
            "ALTER TABLE embeddings ALTER COLUMN source_type TYPE TEXT"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE embeddings ALTER COLUMN source_type TYPE VARCHAR(10)"
        )
    )
