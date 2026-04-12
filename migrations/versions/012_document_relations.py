"""Migration 012 — Cross-referência e Grafo de Conhecimento Leve (Fase 6 NOVO_RAG).

Cria a tabela `document_relations` para armazenar relações semânticas entre
páginas do portal de transparência do TRE-PI.

Tipos de relação:
  referencia   — documento A menciona/cita documento B
  atualiza     — documento A é versão mais recente de B
  substitui    — documento A revoga/substitui B
  complementa  — documentos tratam do mesmo assunto de forma complementar
  origina      — documento A deu origem a B (ex: licitação → contrato)
  decorre_de   — documento A decorre de B (ex: contrato → licitação)

Revision ID: f6a7b9c0d1e2
Revises: e5f6a8b9c0d1
Create Date: 2026-04-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b9c0d1e2"
down_revision: str | None = "e5f6a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            CREATE TABLE document_relations (
                id              SERIAL PRIMARY KEY,
                source_page_id  INTEGER NOT NULL,
                target_page_id  INTEGER,
                target_url      TEXT,
                relation_type   TEXT NOT NULL,
                context         TEXT,
                entity_key      TEXT,
                confidence      REAL NOT NULL DEFAULT 1.0,
                strategy        TEXT NOT NULL DEFAULT 'entity',
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX idx_dr_source   ON document_relations(source_page_id)"))
    op.execute(sa.text("CREATE INDEX idx_dr_target   ON document_relations(target_page_id)"))
    op.execute(sa.text("CREATE INDEX idx_dr_entity   ON document_relations(entity_key)"))
    op.execute(sa.text("CREATE INDEX idx_dr_type     ON document_relations(relation_type)"))


def downgrade() -> None:
    op.execute(sa.text("DROP INDEX IF EXISTS idx_dr_type"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_dr_entity"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_dr_target"))
    op.execute(sa.text("DROP INDEX IF EXISTS idx_dr_source"))
    op.execute(sa.text("DROP TABLE IF EXISTS document_relations"))
