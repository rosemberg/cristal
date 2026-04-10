"""Output port: AnalyticsRepository ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from uuid import UUID


class AnalyticsRepository(ABC):
    @abstractmethod
    async def log_query(
        self,
        session_id: UUID | None,
        query: str,
        intent_type: str,
        pages_found: int,
        chunks_found: int,
        tables_found: int,
        response_time_ms: int,
    ) -> int: ...

    @abstractmethod
    async def update_feedback(self, query_id: int, feedback: str) -> None: ...

    @abstractmethod
    async def get_metrics(self, days: int = 30) -> dict[str, object]: ...

    @abstractmethod
    async def get_daily_stats(self, days: int = 30) -> list[dict[str, object]]: ...
