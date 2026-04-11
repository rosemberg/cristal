"""Unit tests for DocumentService — Etapa 6 (TDD RED → GREEN)."""

from __future__ import annotations

import pytest

from app.domain.entities.document import Document
from app.domain.entities.document_table import DocumentTable


class TestDocumentService:
    @pytest.fixture
    def service(self, sample_document_repo):
        from app.domain.services.document_service import DocumentService

        return DocumentService(document_repo=sample_document_repo)

    # ------------------------------------------------------------------
    # list_documents
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_documents_returns_all(self, service, sample_documents):
        docs = await service.list_documents()
        assert len(docs) == len(sample_documents)

    @pytest.mark.asyncio
    async def test_list_documents_returns_document_instances(self, service):
        docs = await service.list_documents()
        assert all(isinstance(d, Document) for d in docs)

    @pytest.mark.asyncio
    async def test_list_documents_filters_by_type(self, service):
        docs = await service.list_documents(doc_type="pdf")
        assert all(d.type == "pdf" for d in docs)

    @pytest.mark.asyncio
    async def test_list_documents_pagination(self, service, sample_documents):
        page1 = await service.list_documents(page=1, size=2)
        page2 = await service.list_documents(page=2, size=2)
        assert len(page1) <= 2
        assert len(page2) <= 2
        if page1 and page2:
            assert page1[0].document_url != page2[0].document_url

    @pytest.mark.asyncio
    async def test_list_documents_empty_type_returns_all(self, service, sample_documents):
        docs = await service.list_documents(doc_type=None)
        assert len(docs) == len(sample_documents)

    # ------------------------------------------------------------------
    # get
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_returns_known_document(self, service, sample_documents):
        url = sample_documents[0].document_url
        doc = await service.get(url)
        assert doc is not None
        assert doc.document_url == url

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown(self, service):
        doc = await service.get("https://inexistente.example.com/doc.pdf")
        assert doc is None

    # ------------------------------------------------------------------
    # get_content
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_content_returns_string(self, service, sample_documents):
        url = sample_documents[0].document_url
        content = await service.get_content(url)
        assert isinstance(content, str)
        assert len(content) > 0

    @pytest.mark.asyncio
    async def test_get_content_concatenates_chunks(self, service, sample_documents):
        url = sample_documents[0].document_url
        expected_text = sample_documents[0].chunks[0].text
        content = await service.get_content(url)
        assert expected_text in content

    @pytest.mark.asyncio
    async def test_get_content_returns_none_for_unknown(self, service):
        content = await service.get_content("https://inexistente.example.com/doc.pdf")
        assert content is None

    # ------------------------------------------------------------------
    # get_tables
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_tables_returns_list(self, service, sample_documents):
        url = sample_documents[0].document_url
        tables = await service.get_tables(url)
        assert isinstance(tables, list)

    @pytest.mark.asyncio
    async def test_get_tables_returns_document_table_instances(self, service, sample_documents):
        url = sample_documents[0].document_url
        tables = await service.get_tables(url)
        assert all(isinstance(t, DocumentTable) for t in tables)

    @pytest.mark.asyncio
    async def test_get_tables_returns_empty_for_unknown(self, service):
        tables = await service.get_tables("https://inexistente.example.com/doc.pdf")
        assert tables == []
