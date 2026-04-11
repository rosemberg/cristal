"""Application service: SearchService — implements SearchUseCase."""

from __future__ import annotations

from app.domain.ports.inbound.search_use_case import SearchUseCase
from app.domain.ports.outbound.search_repository import SearchRepository
from app.domain.value_objects.search_result import HybridSearchResult, PageMatch


class SearchService(SearchUseCase):
    """Orchestrates keyword/FTS search through the SearchRepository outbound port."""

    def __init__(self, search_repo: SearchRepository) -> None:
        self._repo = search_repo

    async def search_pages(self, query: str, top_k: int = 5) -> list[PageMatch]:
        return await self._repo.search_pages(query, top_k=top_k)

    async def search_documents(
        self, query: str, top_k: int = 5
    ) -> HybridSearchResult:
        pages = await self._repo.search_pages(query, top_k=top_k)
        chunks = await self._repo.search_chunks(query, top_k=top_k)
        tables = await self._repo.search_tables(query)
        return HybridSearchResult(pages=pages, chunks=chunks, tables=tables)
