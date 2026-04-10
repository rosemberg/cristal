"""Input port: SessionUseCase ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.entities.session import ChatSession
from app.domain.value_objects.chat_message import ChatMessage


class SessionUseCase(ABC):
    @abstractmethod
    async def create(self, title: str | None = None) -> ChatSession: ...

    @abstractmethod
    async def get(self, session_id: UUID) -> ChatSession | None: ...

    @abstractmethod
    async def list_messages(self, session_id: UUID) -> list[ChatMessage]: ...
