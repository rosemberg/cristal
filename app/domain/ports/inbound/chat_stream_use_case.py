"""Input port: ChatStreamUseCase — chat com streaming de progresso via SSE."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from uuid import UUID

from app.domain.value_objects.progress_event import ProgressEvent


class ChatStreamUseCase(ABC):
    """Port para chat com streaming de progresso via SSE."""

    @abstractmethod
    async def process_message_stream(
        self,
        message: str,
        session_id: UUID | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> AsyncIterator[ProgressEvent]:
        """Processa mensagem emitindo ProgressEvents a cada etapa do pipeline.

        Implementações podem ser async generators (yield) ou retornar um
        AsyncIterator explícito. Consuma com `async for event in svc.process_message_stream(...)`.
        """
        # pragma: no cover
        return
        yield  # torna o método um async generator para compatibilidade de tipo
