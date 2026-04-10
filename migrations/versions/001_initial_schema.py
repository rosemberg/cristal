"""Schema inicial do PostgreSQL — Cristal 2.0.

Cria todas as tabelas, índices, triggers, funções e views do sistema:
- pages, documents, page_links, navigation_tree (knowledge)
- document_contents, document_chunks, document_tables (RAG)
- chat_sessions, chat_messages (sessões persistentes)
- query_logs (analytics)
- Views: transparency_stats, transparency_map
- Extensão: pg_trgm

Revision ID: a1b2c3d4e5f6
Revises: —
Create Date: 2026-04-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# ─── Alembic metadata ─────────────────────────────────────────────────────────

revision: str = "a1b2c3d4e5f6"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# ─── Upgrade ──────────────────────────────────────────────────────────────────


def upgrade() -> None:
    """Aplica schema completo do Cristal 2.0."""

    # ── Extensões ────────────────────────────────────────────────────────────
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    # ── Tabela: pages ─────────────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE pages (
                id              SERIAL PRIMARY KEY,
                url             TEXT UNIQUE NOT NULL,
                title           TEXT NOT NULL,
                description     TEXT,
                main_content    TEXT,
                content_summary TEXT,
                category        TEXT,
                subcategory     TEXT,
                content_type    TEXT DEFAULT 'page',
                depth           INTEGER DEFAULT 0,
                parent_url      TEXT,
                breadcrumb      JSONB DEFAULT '[]',
                tags            TEXT[] DEFAULT '{}',
                search_vector   TSVECTOR,
                last_modified   TIMESTAMPTZ,
                extracted_at    TIMESTAMPTZ DEFAULT NOW(),
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                updated_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )

    op.execute(sa.text("CREATE INDEX idx_pages_category ON pages(category)"))
    op.execute(sa.text("CREATE INDEX idx_pages_content_type ON pages(content_type)"))
    op.execute(sa.text("CREATE INDEX idx_pages_search ON pages USING GIN(search_vector)"))
    op.execute(sa.text("CREATE INDEX idx_pages_tags ON pages USING GIN(tags)"))
    op.execute(
        sa.text("CREATE INDEX idx_pages_trgm_title ON pages USING GIN(title gin_trgm_ops)")
    )

    # Trigger: atualiza search_vector automaticamente em pages
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
            CREATE TRIGGER pages_search_update
                BEFORE INSERT OR UPDATE ON pages
                FOR EACH ROW EXECUTE FUNCTION pages_search_trigger()
            """
        )
    )

    # ── Tabela: documents ─────────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE documents (
                id              SERIAL PRIMARY KEY,
                page_url        TEXT NOT NULL REFERENCES pages(url) ON DELETE CASCADE,
                document_url    TEXT NOT NULL,
                document_title  TEXT,
                document_type   TEXT,
                context         TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(page_url, document_url)
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX idx_documents_page ON documents(page_url)"))
    op.execute(sa.text("CREATE INDEX idx_documents_type ON documents(document_type)"))

    # ── Tabela: page_links ────────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE page_links (
                id          SERIAL PRIMARY KEY,
                source_url  TEXT NOT NULL REFERENCES pages(url) ON DELETE CASCADE,
                target_url  TEXT NOT NULL,
                link_title  TEXT,
                link_type   TEXT DEFAULT 'internal',
                UNIQUE(source_url, target_url)
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX idx_page_links_source ON page_links(source_url)"))

    # ── Tabela: navigation_tree ───────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE navigation_tree (
                id          SERIAL PRIMARY KEY,
                parent_url  TEXT NOT NULL,
                child_url   TEXT NOT NULL,
                child_title TEXT,
                sort_order  INTEGER DEFAULT 0,
                UNIQUE(parent_url, child_url)
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX idx_nav_parent ON navigation_tree(parent_url)"))

    # ── Tabela: document_contents ─────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE document_contents (
                id                  SERIAL PRIMARY KEY,
                document_url        TEXT NOT NULL,
                page_url            TEXT REFERENCES pages(url) ON DELETE SET NULL,
                document_title      TEXT,
                document_type       TEXT,
                full_text           TEXT,
                num_pages           INTEGER,
                file_size_bytes     BIGINT,
                processing_status   TEXT DEFAULT 'pending',
                error_message       TEXT,
                extracted_at        TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(document_url)
            )
            """
        )
    )

    # ── Tabela: document_chunks ───────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE document_chunks (
                id              SERIAL PRIMARY KEY,
                document_url    TEXT NOT NULL
                                    REFERENCES document_contents(document_url)
                                    ON DELETE CASCADE,
                chunk_index     INTEGER NOT NULL,
                chunk_text      TEXT NOT NULL,
                section_title   TEXT,
                page_number     INTEGER,
                token_count     INTEGER,
                search_vector   TSVECTOR,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )
    op.execute(
        sa.text("CREATE INDEX idx_chunks_document ON document_chunks(document_url)")
    )
    op.execute(
        sa.text("CREATE INDEX idx_chunks_search ON document_chunks USING GIN(search_vector)")
    )

    # Trigger: atualiza search_vector em document_chunks
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
    op.execute(
        sa.text(
            """
            CREATE TRIGGER chunks_search_update
                BEFORE INSERT OR UPDATE ON document_chunks
                FOR EACH ROW EXECUTE FUNCTION chunks_search_trigger()
            """
        )
    )

    # ── Tabela: document_tables ───────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE document_tables (
                id              SERIAL PRIMARY KEY,
                document_url    TEXT NOT NULL
                                    REFERENCES document_contents(document_url)
                                    ON DELETE CASCADE,
                table_index     INTEGER NOT NULL,
                page_number     INTEGER,
                headers         JSONB NOT NULL DEFAULT '[]',
                rows            JSONB NOT NULL DEFAULT '[]',
                caption         TEXT,
                num_rows        INTEGER,
                num_cols        INTEGER,
                search_text     TEXT,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )
    op.execute(
        sa.text("CREATE INDEX idx_tables_document ON document_tables(document_url)")
    )

    # ── Tabela: chat_sessions ─────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE chat_sessions (
                id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                title               TEXT,
                message_count       INTEGER DEFAULT 0,
                documents_consulted TEXT[] DEFAULT '{}',
                created_at          TIMESTAMPTZ DEFAULT NOW(),
                last_active         TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )
    op.execute(
        sa.text("CREATE INDEX idx_sessions_active ON chat_sessions(last_active DESC)")
    )

    # ── Tabela: chat_messages ─────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE chat_messages (
                id          SERIAL PRIMARY KEY,
                session_id  UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
                role        TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content     TEXT NOT NULL,
                metadata    JSONB DEFAULT '{}',
                created_at  TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX idx_messages_session ON chat_messages(session_id, created_at)"
        )
    )

    # ── Tabela: query_logs ────────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE TABLE query_logs (
                id              SERIAL PRIMARY KEY,
                session_id      UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
                query           TEXT NOT NULL,
                intent_type     TEXT,
                pages_found     INTEGER DEFAULT 0,
                chunks_found    INTEGER DEFAULT 0,
                tables_found    INTEGER DEFAULT 0,
                response_time_ms INTEGER,
                feedback        TEXT CHECK (feedback IN ('positive', 'negative', NULL)),
                created_at      TIMESTAMPTZ DEFAULT NOW()
            )
            """
        )
    )
    op.execute(sa.text("CREATE INDEX idx_logs_created ON query_logs(created_at DESC)"))
    op.execute(sa.text("CREATE INDEX idx_logs_intent ON query_logs(intent_type)"))
    # DATE(timestamptz) é STABLE, não IMMUTABLE — usa BRIN que não requer imutabilidade
    # Permite queries de analytics por faixa de datas com custo mínimo de armazenamento
    op.execute(
        sa.text("CREATE INDEX idx_logs_created_date ON query_logs USING BRIN(created_at)")
    )

    # ── Views ─────────────────────────────────────────────────────────────────
    op.execute(
        sa.text(
            """
            CREATE OR REPLACE VIEW transparency_stats AS
            SELECT
                (SELECT COUNT(*) FROM pages)               AS total_pages,
                (SELECT COUNT(*) FROM documents)           AS total_documents,
                (SELECT COUNT(DISTINCT category)
                   FROM pages WHERE category IS NOT NULL)  AS total_categories,
                (SELECT COUNT(*) FROM page_links)          AS total_links,
                (SELECT COUNT(*) FROM document_contents
                  WHERE processing_status = 'done')        AS documents_processed,
                (SELECT COUNT(*) FROM document_chunks)     AS total_chunks,
                (SELECT COUNT(*) FROM document_tables)     AS total_tables,
                (SELECT COUNT(*) FROM chat_sessions)       AS total_sessions
            """
        )
    )

    op.execute(
        sa.text(
            """
            CREATE OR REPLACE VIEW transparency_map AS
            SELECT
                p.category,
                p.subcategory,
                COUNT(DISTINCT p.id)    AS page_count,
                COUNT(DISTINCT d.id)    AS document_count,
                ARRAY_AGG(DISTINCT p.content_type)
                    FILTER (WHERE p.content_type IS NOT NULL) AS content_types
            FROM pages p
            LEFT JOIN documents d ON d.page_url = p.url
            WHERE p.category IS NOT NULL
            GROUP BY p.category, p.subcategory
            ORDER BY p.category, p.subcategory
            """
        )
    )


# ─── Downgrade ────────────────────────────────────────────────────────────────


def downgrade() -> None:
    """Remove todo o schema criado por esta migration."""

    # Views
    op.execute(sa.text("DROP VIEW IF EXISTS transparency_map"))
    op.execute(sa.text("DROP VIEW IF EXISTS transparency_stats"))

    # Triggers e funções (CASCADE remove triggers associados)
    op.execute(sa.text("DROP TRIGGER IF EXISTS chunks_search_update ON document_chunks"))
    op.execute(sa.text("DROP TRIGGER IF EXISTS pages_search_update ON pages"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS chunks_search_trigger()"))
    op.execute(sa.text("DROP FUNCTION IF EXISTS pages_search_trigger()"))

    # Tabelas (ordem: filhas antes das pais)
    op.execute(sa.text("DROP TABLE IF EXISTS query_logs CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS chat_messages CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS chat_sessions CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS document_tables CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS document_chunks CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS document_contents CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS navigation_tree CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS page_links CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS documents CASCADE"))
    op.execute(sa.text("DROP TABLE IF EXISTS pages CASCADE"))

    # Extensão (apenas pg_trgm — adicionada por esta migration)
    op.execute(sa.text("DROP EXTENSION IF EXISTS pg_trgm"))
