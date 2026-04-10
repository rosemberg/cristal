"""Input port: SearchUseCase ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.value_objects.search_result import HybridSearchResult, PageMatch


class SearchUseCase(ABC):
    @abstractmethod
    async def search_pages(self, query: str, top_k: int = 5) -> list[PageMatch]: ...

    @abstractmethod
    async def search_documents(
        self, query: str, top_k: int = 5
    ) -> HybridSearchResult: ...
