"""Output port: SessionRepository ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.session import ChatSession
from app.domain.value_objects.chat_message import ChatMessage


class SessionRepository(ABC):
    @abstractmethod
    async def create(self, title: str | None = None) -> ChatSession: ...

    @abstractmethod
    async def get(self, session_id: UUID) -> ChatSession | None: ...

    @abstractmethod
    async def save_message(self, session_id: UUID, message: ChatMessage) -> None: ...

    @abstractmethod
    async def list_sessions(self, limit: int = 20) -> list[ChatSession]: ...
