"""Integration tests — Chat API (RED → GREEN).

Testa POST /api/chat e GET /api/suggest usando fakes injetados via
dependency_overrides. Nenhum banco real ou LLM é chamado.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.ports.inbound.chat_use_case import ChatUseCase
from app.domain.value_objects.chat_message import ChatMessage, Citation, TableData


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeChatUseCase(ChatUseCase):
    async def process_message(
        self,
        message: str,
        session_id: UUID | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=f"Resposta para: {message}",
            sources=[
                Citation(
                    document_title="Doc Teste",
                    document_url="https://www.tre-pi.jus.br/doc.pdf",
                    snippet="Trecho relevante",
                    page_number=1,
                )
            ],
            tables=[
                TableData(
                    headers=["A", "B"],
                    rows=[["1", "2"]],
                    source_document="doc.pdf",
                    title="Tabela 1",
                    page_number=1,
                )
            ],
        )

    async def get_suggestions(self) -> list[str]:
        return ["Pergunta 1", "Pergunta 2", "Pergunta 3"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    from app.adapters.inbound.fastapi.app import create_app
    from app.adapters.inbound.fastapi.dependencies import get_chat_use_case

    @asynccontextmanager
    async def noop_lifespan(app):  # type: ignore[no-untyped-def]
        yield

    app = create_app(lifespan=noop_lifespan)
    app.dependency_overrides[get_chat_use_case] = lambda: _FakeChatUseCase()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests — POST /api/chat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_post_chat_returns_200(client: AsyncClient) -> None:
    resp = await client.post("/api/chat", json={"message": "Quais licitações existem?"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_chat_returns_text(client: AsyncClient) -> None:
    resp = await client.post("/api/chat", json={"message": "Quais licitações existem?"})
    data = resp.json()
    assert "text" in data
    assert "Quais licitações existem?" in data["text"]


@pytest.mark.asyncio
async def test_post_chat_returns_sources(client: AsyncClient) -> None:
    resp = await client.post("/api/chat", json={"message": "contratos"})
    data = resp.json()
    assert isinstance(data["sources"], list)
    assert len(data["sources"]) == 1
    assert data["sources"][0]["document_title"] == "Doc Teste"


@pytest.mark.asyncio
async def test_post_chat_returns_tables(client: AsyncClient) -> None:
    resp = await client.post("/api/chat", json={"message": "tabelas"})
    data = resp.json()
    assert isinstance(data["tables"], list)
    assert len(data["tables"]) == 1
    assert data["tables"][0]["headers"] == ["A", "B"]


@pytest.mark.asyncio
async def test_post_chat_with_session_id(client: AsyncClient) -> None:
    session_id = str(uuid4())
    resp = await client.post(
        "/api/chat", json={"message": "orçamento", "session_id": session_id}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_post_chat_rejects_empty_message(client: AsyncClient) -> None:
    resp = await client.post("/api/chat", json={"message": ""})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_post_chat_rejects_missing_message(client: AsyncClient) -> None:
    resp = await client.post("/api/chat", json={})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Tests — GET /api/suggest
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_suggest_returns_200(client: AsyncClient) -> None:
    resp = await client.get("/api/suggest")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_suggest_returns_list(client: AsyncClient) -> None:
    resp = await client.get("/api/suggest")
    data = resp.json()
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)
    assert len(data["suggestions"]) == 3
