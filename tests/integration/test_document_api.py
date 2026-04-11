"""Integration tests — Document API (RED → GREEN).

Testa GET /api/documents e sub-rotas usando fakes injetados via
dependency_overrides. Nenhum banco real é usado.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import quote

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.entities.document import Document
from app.domain.entities.document_table import DocumentTable
from app.domain.ports.inbound.document_use_case import DocumentUseCase


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_DOC_URL = "https://www.tre-pi.jus.br/doc-teste.pdf"

_SAMPLE_DOC = Document(
    id=1,
    page_url="https://www.tre-pi.jus.br/pagina",
    document_url=_DOC_URL,
    type="pdf",
    is_processed=True,
    title="Relatório de Gestão 2024",
    num_pages=10,
)

_SAMPLE_TABLE = DocumentTable(
    id=1,
    document_url=_DOC_URL,
    table_index=0,
    headers=["Categoria", "Valor"],
    rows=[["Pessoal", "1000000"]],
    caption="Despesas 2024",
    page_number=3,
)


class _FakeDocumentUseCase(DocumentUseCase):
    async def list_documents(
        self,
        category: str | None = None,
        doc_type: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> list[Document]:
        docs = [_SAMPLE_DOC]
        if doc_type and doc_type != _SAMPLE_DOC.type:
            return []
        return docs

    async def get(self, document_url: str) -> Document | None:
        if document_url == _DOC_URL:
            return _SAMPLE_DOC
        return None

    async def get_content(self, document_url: str) -> str | None:
        if document_url == _DOC_URL:
            return "Conteúdo extraído do documento."
        return None

    async def get_tables(self, document_url: str) -> list[DocumentTable]:
        if document_url == _DOC_URL:
            return [_SAMPLE_TABLE]
        return []


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from app.adapters.inbound.fastapi.app import create_app
    from app.adapters.inbound.fastapi.dependencies import get_document_use_case

    @asynccontextmanager
    async def noop_lifespan(app):  # type: ignore[no-untyped-def]
        yield

    app = create_app(lifespan=noop_lifespan)
    app.dependency_overrides[get_document_use_case] = lambda: _FakeDocumentUseCase()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests — GET /api/documents
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_documents_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/api/documents")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_documents_returns_list(client: AsyncClient) -> None:
    resp = await client.get("/api/documents")
    data = resp.json()
    assert "documents" in data
    assert len(data["documents"]) == 1
    assert data["documents"][0]["title"] == "Relatório de Gestão 2024"


@pytest.mark.asyncio
async def test_get_documents_filter_by_type_match(client: AsyncClient) -> None:
    resp = await client.get("/api/documents", params={"doc_type": "pdf"})
    data = resp.json()
    assert len(data["documents"]) == 1


@pytest.mark.asyncio
async def test_get_documents_filter_by_type_no_match(client: AsyncClient) -> None:
    resp = await client.get("/api/documents", params={"doc_type": "csv"})
    data = resp.json()
    assert len(data["documents"]) == 0


# ---------------------------------------------------------------------------
# Tests — GET /api/documents/{url}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_by_url_returns_200(client: AsyncClient) -> None:
    encoded = quote(_DOC_URL, safe="")
    resp = await client.get(f"/api/documents/{encoded}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_document_by_url_returns_doc(client: AsyncClient) -> None:
    encoded = quote(_DOC_URL, safe="")
    resp = await client.get(f"/api/documents/{encoded}")
    data = resp.json()
    assert data["document_url"] == _DOC_URL
    assert data["title"] == "Relatório de Gestão 2024"


@pytest.mark.asyncio
async def test_get_document_not_found_returns_404(client: AsyncClient) -> None:
    encoded = quote("https://www.tre-pi.jus.br/inexistente.pdf", safe="")
    resp = await client.get(f"/api/documents/{encoded}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — GET /api/documents/{url}/content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_content_returns_200(client: AsyncClient) -> None:
    encoded = quote(_DOC_URL, safe="")
    resp = await client.get(f"/api/documents/{encoded}/content")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_document_content_returns_text(client: AsyncClient) -> None:
    encoded = quote(_DOC_URL, safe="")
    resp = await client.get(f"/api/documents/{encoded}/content")
    data = resp.json()
    assert "content" in data
    assert "Conteúdo extraído" in data["content"]


@pytest.mark.asyncio
async def test_get_document_content_not_found_returns_404(client: AsyncClient) -> None:
    encoded = quote("https://www.tre-pi.jus.br/nao-existe.pdf", safe="")
    resp = await client.get(f"/api/documents/{encoded}/content")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — GET /api/documents/{url}/tables
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_document_tables_returns_200(client: AsyncClient) -> None:
    encoded = quote(_DOC_URL, safe="")
    resp = await client.get(f"/api/documents/{encoded}/tables")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_document_tables_returns_list(client: AsyncClient) -> None:
    encoded = quote(_DOC_URL, safe="")
    resp = await client.get(f"/api/documents/{encoded}/tables")
    data = resp.json()
    assert "tables" in data
    assert len(data["tables"]) == 1
    assert data["tables"][0]["headers"] == ["Categoria", "Valor"]
