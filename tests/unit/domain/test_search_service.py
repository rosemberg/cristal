"""Unit tests for SearchService — Etapa 6 (TDD RED → GREEN)."""

from __future__ import annotations

import pytest

from app.domain.entities.document_table import DocumentTable
from app.domain.value_objects.search_result import ChunkMatch, HybridSearchResult, PageMatch
from tests.conftest import FakeSearchRepository


# ---------------------------------------------------------------------------
# Helpers / extended fakes
# ---------------------------------------------------------------------------


class FakeSearchRepoWithChunks(FakeSearchRepository):
    """Extends FakeSearchRepository to also return chunks."""

    def __init__(self, pages, chunks=None, tables=None):
        super().__init__(pages=pages)
        self._chunks = chunks or []
        self._tables = tables or []

    async def search_chunks(self, query: str, top_k: int = 5) -> list[ChunkMatch]:
        return self._chunks[:top_k]

    async def search_tables(self, query: str) -> list[DocumentTable]:
        return self._tables


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSearchService:
    @pytest.fixture
    def service(self, sample_search_repo):
        from app.domain.services.search_service import SearchService

        return SearchService(search_repo=sample_search_repo)

    @pytest.mark.asyncio
    async def test_search_pages_delegates_to_repo(self, service, sample_pages):
        results = await service.search_pages("Página de teste 1")
        assert isinstance(results, list)
        assert all(isinstance(r, PageMatch) for r in results)

    @pytest.mark.asyncio
    async def test_search_pages_returns_empty_when_no_match(self, service):
        results = await service.search_pages("termo-inexistente-xyz")
        assert results == []

    @pytest.mark.asyncio
    async def test_search_pages_respects_top_k(self, sample_pages):
        from app.domain.services.search_service import SearchService

        repo = FakeSearchRepoWithChunks(pages=sample_pages)
        service = SearchService(search_repo=repo)
        # "teste" matches all 10 sample pages
        results = await service.search_pages("teste", top_k=3)
        assert len(results) <= 3

    @pytest.mark.asyncio
    async def test_search_documents_returns_hybrid_result(self, service):
        result = await service.search_documents("licitação")
        assert isinstance(result, HybridSearchResult)

    @pytest.mark.asyncio
    async def test_search_documents_contains_pages(self, service, sample_pages):
        result = await service.search_documents("Página de teste")
        assert isinstance(result.pages, list)

    @pytest.mark.asyncio
    async def test_search_documents_contains_chunks_and_tables(self, sample_pages, sample_documents):
        from app.domain.entities.chunk import DocumentChunk
        from app.domain.services.search_service import SearchService

        chunk = ChunkMatch(
            chunk=sample_documents[0].chunks[0],
            document_title="Doc 1",
            document_url=sample_documents[0].document_url,
            score=0.9,
        )
        table = sample_documents[0].tables[0]
        repo = FakeSearchRepoWithChunks(pages=sample_pages, chunks=[chunk], tables=[table])
        service = SearchService(search_repo=repo)

        result = await service.search_documents("teste")
        assert len(result.chunks) >= 1
        assert len(result.tables) >= 1

    @pytest.mark.asyncio
    async def test_search_documents_empty_query_returns_structure(self, service):
        result = await service.search_documents("")
        assert hasattr(result, "pages")
        assert hasattr(result, "chunks")
        assert hasattr(result, "tables")
