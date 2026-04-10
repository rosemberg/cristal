"""Output port: SearchRepository ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.document_table import DocumentTable
from app.domain.value_objects.search_result import ChunkMatch, PageMatch


class SearchRepository(ABC):
    @abstractmethod
    async def search_pages(self, query: str, top_k: int = 5) -> list[PageMatch]: ...

    @abstractmethod
    async def search_chunks(self, query: str, top_k: int = 5) -> list[ChunkMatch]: ...

    @abstractmethod
    async def search_tables(self, query: str) -> list[DocumentTable]: ...

    @abstractmethod
    async def get_categories(self) -> list[dict[str, object]]: ...

    @abstractmethod
    async def get_stats(self) -> dict[str, object]: ...
