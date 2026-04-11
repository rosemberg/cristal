"""Application service: SessionService — implements SessionUseCase."""

from __future__ import annotations

from uuid import UUID

from app.domain.entities.session import ChatSession
from app.domain.ports.inbound.session_use_case import SessionUseCase
from app.domain.ports.outbound.session_repository import SessionRepository
from app.domain.value_objects.chat_message import ChatMessage


class SessionService(SessionUseCase):
    """Manages chat session lifecycle through the SessionRepository port."""

    def __init__(self, session_repo: SessionRepository) -> None:
        self._repo = session_repo

    async def create(self, title: str | None = None) -> ChatSession:
        return await self._repo.create(title=title)

    async def get(self, session_id: UUID) -> ChatSession | None:
        return await self._repo.get(session_id)

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]:
        session = await self._repo.get(session_id)
        if session is None:
            return []
        return list(session.messages)
