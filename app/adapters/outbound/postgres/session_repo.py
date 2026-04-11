"""PostgreSQL adapter: SessionRepository.

Gerencia sessões de chat e mensagens com serialização JSONB para sources e tables.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

import asyncpg

from app.domain.entities.session import ChatSession
from app.domain.ports.outbound.session_repository import SessionRepository
from app.domain.value_objects.chat_message import ChatMessage, Citation, TableData

logger = logging.getLogger(__name__)


def _message_to_metadata(message: ChatMessage) -> str:
    """Serializa sources e tables para JSONB."""
    return json.dumps(
        {
            "sources": [
                {
                    "document_title": c.document_title,
                    "document_url": c.document_url,
                    "snippet": c.snippet,
                    "page_number": c.page_number,
                }
                for c in message.sources
            ],
            "tables": [
                {
                    "headers": list(t.headers),
                    "rows": [list(r) for r in t.rows],
                    "source_document": t.source_document,
                    "title": t.title,
                    "page_number": t.page_number,
                }
                for t in message.tables
            ],
        }
    )


def _record_to_message(row: asyncpg.Record) -> ChatMessage:
    """Reconstrói ChatMessage a partir de uma linha do banco."""
    raw = row["metadata"]
    metadata: dict = (json.loads(raw) if isinstance(raw, str) else raw) or {}
    sources = [Citation(**s) for s in metadata.get("sources", [])]
    tables = [TableData(**t) for t in metadata.get("tables", [])]
    return ChatMessage(
        role=row["role"],
        content=row["content"],
        sources=sources,
        tables=tables,
    )


class PostgresSessionRepository(SessionRepository):
    """Persistência de sessões de chat via asyncpg."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create(self, title: str | None = None) -> ChatSession:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO chat_sessions (title)
                VALUES ($1)
                RETURNING id, title, created_at, last_active
                """,
                title,
            )
        assert row is not None
        return ChatSession(
            id=row["id"],
            created_at=row["created_at"],
            last_active=row["last_active"],
            title=row["title"],
        )

    async def get(self, session_id: UUID) -> ChatSession | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id, title, created_at, last_active, documents_consulted
                FROM chat_sessions
                WHERE id = $1
                """,
                session_id,
            )
            if row is None:
                return None
            msg_rows = await conn.fetch(
                """
                SELECT role, content, metadata
                FROM chat_messages
                WHERE session_id = $1
                ORDER BY created_at
                """,
                session_id,
            )
        return ChatSession(
            id=row["id"],
            created_at=row["created_at"],
            last_active=row["last_active"],
            title=row["title"],
            messages=[_record_to_message(r) for r in msg_rows],
            documents_consulted=(
                list(row["documents_consulted"]) if row["documents_consulted"] else []
            ),
        )

    async def save_message(self, session_id: UUID, message: ChatMessage) -> None:
        metadata = _message_to_metadata(message)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO chat_messages (session_id, role, content, metadata)
                    VALUES ($1, $2, $3, $4::jsonb)
                    """,
                    session_id,
                    message.role,
                    message.content,
                    metadata,
                )
                await conn.execute(
                    """
                    UPDATE chat_sessions
                    SET message_count = message_count + 1,
                        last_active   = NOW()
                    WHERE id = $1
                    """,
                    session_id,
                )

    async def list_sessions(self, limit: int = 20) -> list[ChatSession]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id, title, created_at, last_active, documents_consulted
                FROM chat_sessions
                ORDER BY last_active DESC
                LIMIT $1
                """,
                limit,
            )
        return [
            ChatSession(
                id=r["id"],
                created_at=r["created_at"],
                last_active=r["last_active"],
                title=r["title"],
                documents_consulted=(
                    list(r["documents_consulted"]) if r["documents_consulted"] else []
                ),
            )
            for r in rows
        ]
