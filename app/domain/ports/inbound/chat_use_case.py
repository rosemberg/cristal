"""Input port: ChatUseCase ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID

from app.domain.value_objects.chat_message import ChatMessage


class ChatUseCase(ABC):
    @abstractmethod
    async def process_message(
        self,
        message: str,
        session_id: UUID | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> ChatMessage: ...

    @abstractmethod
    async def get_suggestions(self) -> list[str]: ...
