"""PostgreSQL adapter: DocumentRepository.

Gerencia documentos, chunks e tabelas extraídas via asyncpg.
"""

from __future__ import annotations

import json
import logging

import asyncpg

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document import Document
from app.domain.entities.document_table import DocumentTable
from app.domain.ports.outbound.document_repository import (
    DocumentCheckInfo,
    DocumentRepository,
    ProcessedDocument,
)

logger = logging.getLogger(__name__)

_VALID_STATUSES = frozenset({"pending", "processing", "done", "error"})


def _parse_jsonb(value: object) -> list:  # type: ignore[type-arg]
    """Normaliza valor JSONB: asyncpg pode retornar string ou lista nativa."""
    if value is None:
        return []
    if isinstance(value, str):
        return json.loads(value)  # type: ignore[no-any-return]
    return list(value)  # type: ignore[arg-type]


def _record_to_document(row: asyncpg.Record) -> Document:
    return Document(
        id=row["id"],
        page_url=row["page_url"],
        document_url=row["document_url"],
        type=row["document_type"] or "pdf",
        is_processed=row["processing_status"] == "done",
        title=row["document_title"],
        num_pages=row.get("num_pages"),
    )


class PostgresDocumentRepository(DocumentRepository):
    """Acesso a documentos e conteúdo RAG via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def find_by_url(self, url: str) -> Document | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT d.id,
                       d.page_url,
                       d.document_url,
                       d.document_type,
                       d.document_title,
                       dc.num_pages,
                       dc.processing_status
                FROM documents d
                LEFT JOIN document_contents dc ON dc.document_url = d.document_url
                WHERE d.document_url = $1
                LIMIT 1
                """,
                url,
            )
        if row is None:
            return None
        return _record_to_document(row)

    async def list_documents(
        self,
        category: str | None = None,
        doc_type: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> list[Document]:
        offset = (page - 1) * size
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT d.id,
                       d.page_url,
                       d.document_url,
                       d.document_type,
                       d.document_title,
                       dc.num_pages,
                       dc.processing_status
                FROM documents d
                LEFT JOIN document_contents dc ON dc.document_url = d.document_url
                LEFT JOIN pages p ON p.url = d.page_url
                WHERE ($1::text IS NULL OR p.category = $1)
                  AND ($2::text IS NULL OR d.document_type = $2)
                ORDER BY d.id
                LIMIT $3 OFFSET $4
                """,
                category,
                doc_type,
                size,
                offset,
            )
        return [_record_to_document(r) for r in rows]

    async def get_chunks(self, document_url: str) -> list[DocumentChunk]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, document_url, chunk_index, chunk_text,
                       token_count, section_title, page_number
                FROM document_chunks
                WHERE document_url = $1
                ORDER BY chunk_index
                """,
                document_url,
            )
        return [
            DocumentChunk(
                id=r["id"],
                document_url=r["document_url"],
                chunk_index=r["chunk_index"],
                text=r["chunk_text"],
                token_count=r["token_count"] or 0,
                section_title=r["section_title"],
                page_number=r["page_number"],
            )
            for r in rows
        ]

    async def get_tables(self, document_url: str) -> list[DocumentTable]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, document_url, table_index, headers, rows,
                       page_number, caption, num_rows, num_cols
                FROM document_tables
                WHERE document_url = $1
                ORDER BY table_index
                """,
                document_url,
            )
        return [
            DocumentTable(
                id=r["id"],
                document_url=r["document_url"],
                table_index=r["table_index"],
                headers=_parse_jsonb(r["headers"]),
                rows=[list(row) for row in _parse_jsonb(r["rows"])],
                page_number=r["page_number"],
                caption=r["caption"],
                num_rows=r["num_rows"],
                num_cols=r["num_cols"],
            )
            for r in rows
        ]

    async def list_pending(self, limit: int = 0) -> list[Document]:
        _SELECT = """
            SELECT d.id,
                   d.page_url,
                   d.document_url,
                   d.document_type,
                   d.document_title,
                   d.processing_status,
                   dc.num_pages
            FROM documents d
            LEFT JOIN document_contents dc ON dc.document_url = d.document_url
            WHERE d.processing_status = 'pending'
            ORDER BY d.created_at ASC
        """
        async with self._pool.acquire() as conn:
            if limit > 0:
                rows = await conn.fetch(_SELECT + " LIMIT $1", limit)
            else:
                rows = await conn.fetch(_SELECT)
        return [_record_to_document(r) for r in rows]

    async def list_errors(self) -> list[Document]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT d.id,
                       d.page_url,
                       d.document_url,
                       d.document_type,
                       d.document_title,
                       d.processing_status,
                       dc.num_pages
                FROM documents d
                LEFT JOIN document_contents dc ON dc.document_url = d.document_url
                WHERE d.processing_status = 'error'
                ORDER BY d.created_at ASC
                """,
            )
        return [_record_to_document(r) for r in rows]

    async def reset_stuck_processing(self, stuck_minutes: int = 30) -> int:
        async with self._pool.acquire() as conn:
            if stuck_minutes > 0:
                result = await conn.execute(
                    """
                    UPDATE documents
                    SET processing_status = 'pending',
                        processing_error  = NULL,
                        processed_at      = NOW()
                    WHERE processing_status = 'processing'
                      AND processed_at < NOW() - ($1 * interval '1 minute')
                    """,
                    stuck_minutes,
                )
            else:
                result = await conn.execute(
                    """
                    UPDATE documents
                    SET processing_status = 'pending',
                        processing_error  = NULL,
                        processed_at      = NOW()
                    WHERE processing_status = 'processing'
                    """
                )
        # asyncpg retorna "UPDATE N"
        return int(result.split()[-1])

    async def update_status(
        self, document_url: str, status: str, error: str | None = None
    ) -> None:
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"Status inválido: {status!r}. Valores aceitos: {sorted(_VALID_STATUSES)}"
            )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE documents
                SET processing_status = $2,
                    processing_error  = $3,
                    processed_at      = NOW()
                WHERE document_url = $1
                """,
                document_url,
                status,
                error,
            )

    async def save_content_atomic(
        self, document_url: str, content: ProcessedDocument
    ) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Limpa conteúdo anterior
                await conn.execute(
                    "DELETE FROM document_chunks WHERE document_url = $1", document_url
                )
                await conn.execute(
                    "DELETE FROM document_tables WHERE document_url = $1", document_url
                )
                await conn.execute(
                    "DELETE FROM document_contents WHERE document_url = $1", document_url
                )
                # Insere conteúdo
                await conn.execute(
                    """
                    INSERT INTO document_contents
                        (document_url, document_title, full_text, num_pages, processing_status)
                    VALUES ($1, $2, $3, $4, 'done')
                    ON CONFLICT (document_url) DO UPDATE SET
                        document_title   = EXCLUDED.document_title,
                        full_text        = EXCLUDED.full_text,
                        num_pages        = EXCLUDED.num_pages,
                        processing_status = EXCLUDED.processing_status
                    """,
                    document_url,
                    content.title,
                    content.text,
                    content.num_pages,
                )
                if content.chunks:
                    await conn.executemany(
                        """
                        INSERT INTO document_chunks
                            (document_url, chunk_index, chunk_text,
                             section_title, page_number, token_count,
                             version, has_table, parent_chunk_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        """,
                        [
                            (
                                document_url,
                                c.chunk_index,
                                c.text,
                                c.section_title,
                                c.page_number,
                                c.token_count,
                                c.version,
                                c.has_table,
                                c.parent_chunk_id,
                            )
                            for c in content.chunks
                        ],
                    )
                if content.tables:
                    await conn.executemany(
                        """
                        INSERT INTO document_tables
                            (document_url, table_index, headers, rows,
                             caption, num_rows, num_cols)
                        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7)
                        """,
                        [
                            (
                                document_url,
                                t.table_index,
                                json.dumps(t.headers),
                                json.dumps(t.rows),
                                t.caption,
                                t.num_rows,
                                t.num_cols,
                            )
                            for t in content.tables
                        ],
                    )

    async def count_by_status(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT processing_status, COUNT(*) AS total
                FROM documents
                GROUP BY processing_status
                """
            )
        return {row["processing_status"]: row["total"] for row in rows}

    async def count_chunks(self) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) AS total FROM document_chunks")
        return row["total"] if row else 0

    async def count_tables(self) -> int:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT COUNT(*) AS total FROM document_tables")
        return row["total"] if row else 0

    async def list_done(self) -> list[DocumentCheckInfo]:
        """Retorna documentos com processing_status='done' para health check."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT d.document_url,
                       d.document_title,
                       d.page_url
                FROM documents d
                WHERE d.processing_status = 'done'
                ORDER BY d.document_url
                """
            )
        return [
            DocumentCheckInfo(
                url=r["document_url"],
                title=r["document_title"],
                page_url=r["page_url"],
                stored_content_length=None,
            )
            for r in rows
        ]

    async def save_content(self, document_url: str, content: ProcessedDocument) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Upsert em document_contents
                await conn.execute(
                    """
                    INSERT INTO document_contents
                        (document_url, document_title, full_text,
                         num_pages, processing_status)
                    VALUES ($1, $2, $3, $4, 'done')
                    ON CONFLICT (document_url) DO UPDATE SET
                        document_title    = EXCLUDED.document_title,
                        full_text         = EXCLUDED.full_text,
                        num_pages         = EXCLUDED.num_pages,
                        processing_status = 'done',
                        extracted_at      = NOW()
                    """,
                    document_url,
                    content.title,
                    content.text,
                    content.num_pages,
                )
                # Substitui chunks
                await conn.execute(
                    "DELETE FROM document_chunks WHERE document_url = $1",
                    document_url,
                )
                if content.chunks:
                    await conn.executemany(
                        """
                        INSERT INTO document_chunks
                            (document_url, chunk_index, chunk_text,
                             section_title, page_number, token_count,
                             version, has_table, parent_chunk_id)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        """,
                        [
                            (
                                document_url,
                                c.chunk_index,
                                c.text,
                                c.section_title,
                                c.page_number,
                                c.token_count,
                                c.version,
                                c.has_table,
                                c.parent_chunk_id,
                            )
                            for c in content.chunks
                        ],
                    )
                # Substitui tabelas
                await conn.execute(
                    "DELETE FROM document_tables WHERE document_url = $1",
                    document_url,
                )
                if content.tables:
                    await conn.executemany(
                        """
                        INSERT INTO document_tables
                            (document_url, table_index, headers, rows,
                             caption, num_rows, num_cols)
                        VALUES ($1, $2, $3::jsonb, $4::jsonb, $5, $6, $7)
                        """,
                        [
                            (
                                document_url,
                                t.table_index,
                                json.dumps(t.headers),
                                json.dumps(t.rows),
                                t.caption,
                                t.num_rows,
                                t.num_cols,
                            )
                            for t in content.tables
                        ],
                    )
