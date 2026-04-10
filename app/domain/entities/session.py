"""Domain entity: ChatSession."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from app.domain.value_objects.chat_message import ChatMessage


@dataclass
class ChatSession:
    id: UUID
    created_at: datetime
    last_active: datetime
    title: str | None = None
    messages: list[ChatMessage] = field(default_factory=list)
    documents_consulted: list[str] = field(default_factory=list)

    def add_message(self, message: ChatMessage, max_history: int = 10) -> None:
        self.messages.append(message)
        if len(self.messages) > max_history:
            self.messages = self.messages[-max_history:]
        self.last_active = datetime.now(UTC)
