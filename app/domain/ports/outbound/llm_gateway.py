"""Output port: LLMGateway ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class LLMGateway(ABC):
    @abstractmethod
    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
        temperature: float = 0.3,
    ) -> str: ...

    @abstractmethod
    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
    ) -> AsyncIterator[str]: ...
