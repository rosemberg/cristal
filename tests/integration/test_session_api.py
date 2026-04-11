"""Integration tests — Session API (RED → GREEN).

Testa POST /api/sessions e GET /api/sessions/{id} usando fakes injetados
via dependency_overrides. Nenhum banco real é usado.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import AsyncGenerator
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from app.domain.entities.session import ChatSession
from app.domain.ports.inbound.session_use_case import SessionUseCase
from app.domain.value_objects.chat_message import ChatMessage


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_FIXED_SESSION_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
_FIXED_CREATED_AT = datetime(2024, 1, 15, 10, 0, 0, tzinfo=UTC)


class _FakeSessionUseCase(SessionUseCase):
    def __init__(self) -> None:
        self._sessions: dict[UUID, ChatSession] = {}

    async def create(self, title: str | None = None) -> ChatSession:
        session = ChatSession(
            id=_FIXED_SESSION_ID,
            created_at=_FIXED_CREATED_AT,
            last_active=_FIXED_CREATED_AT,
            title=title,
        )
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: UUID) -> ChatSession | None:
        return self._sessions.get(session_id)

    async def list_messages(self, session_id: UUID) -> list[ChatMessage]:
        session = self._sessions.get(session_id)
        if session is None:
            return []
        return list(session.messages)

    async def list_sessions(self, limit: int = 20) -> list[ChatSession]:
        return sorted(self._sessions.values(), key=lambda s: s.last_active, reverse=True)[:limit]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_session_uc() -> _FakeSessionUseCase:
    return _FakeSessionUseCase()


@pytest.fixture
async def client(fake_session_uc: _FakeSessionUseCase) -> AsyncGenerator[AsyncClient, None]:
    from app.adapters.inbound.fastapi.app import create_app
    from app.adapters.inbound.fastapi.dependencies import get_session_use_case

    @asynccontextmanager
    async def noop_lifespan(app):  # type: ignore[no-untyped-def]
        yield

    app = create_app(lifespan=noop_lifespan)
    app.dependency_overrides[get_session_use_case] = lambda: fake_session_uc

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Tests — POST /api/sessions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_session_returns_201(client: AsyncClient) -> None:
    resp = await client.post("/api/sessions", json={})
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_create_session_returns_id(client: AsyncClient) -> None:
    resp = await client.post("/api/sessions", json={})
    data = resp.json()
    assert "id" in data
    assert str(data["id"]) == str(_FIXED_SESSION_ID)


@pytest.mark.asyncio
async def test_create_session_with_title(client: AsyncClient) -> None:
    resp = await client.post("/api/sessions", json={"title": "Minha conversa"})
    data = resp.json()
    assert data["title"] == "Minha conversa"


@pytest.mark.asyncio
async def test_create_session_without_title(client: AsyncClient) -> None:
    resp = await client.post("/api/sessions", json={})
    data = resp.json()
    assert data["title"] is None


# ---------------------------------------------------------------------------
# Tests — GET /api/sessions/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_session_returns_200(
    client: AsyncClient, fake_session_uc: _FakeSessionUseCase
) -> None:
    # Criar sessão primeiro
    await fake_session_uc.create(title="Teste")
    resp = await client.get(f"/api/sessions/{_FIXED_SESSION_ID}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_session_returns_data(
    client: AsyncClient, fake_session_uc: _FakeSessionUseCase
) -> None:
    await fake_session_uc.create(title="Consulta orçamento")
    resp = await client.get(f"/api/sessions/{_FIXED_SESSION_ID}")
    data = resp.json()
    assert str(data["id"]) == str(_FIXED_SESSION_ID)
    assert data["title"] == "Consulta orçamento"


@pytest.mark.asyncio
async def test_get_session_not_found_returns_404(client: AsyncClient) -> None:
    unknown_id = uuid.uuid4()
    resp = await client.get(f"/api/sessions/{unknown_id}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Tests — GET /api/sessions/{id}/messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_messages_returns_200(
    client: AsyncClient, fake_session_uc: _FakeSessionUseCase
) -> None:
    await fake_session_uc.create()
    resp = await client.get(f"/api/sessions/{_FIXED_SESSION_ID}/messages")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_messages_empty(
    client: AsyncClient, fake_session_uc: _FakeSessionUseCase
) -> None:
    await fake_session_uc.create()
    resp = await client.get(f"/api/sessions/{_FIXED_SESSION_ID}/messages")
    data = resp.json()
    assert "messages" in data
    assert data["messages"] == []


@pytest.mark.asyncio
async def test_list_messages_unknown_session_returns_empty(
    client: AsyncClient,
) -> None:
    unknown_id = uuid.uuid4()
    resp = await client.get(f"/api/sessions/{unknown_id}/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"] == []


# ---------------------------------------------------------------------------
# Tests — GET /api/sessions (list)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_sessions_empty(client: AsyncClient) -> None:
    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert "sessions" in data
    assert data["sessions"] == []


@pytest.mark.asyncio
async def test_list_sessions_returns_created(
    client: AsyncClient, fake_session_uc: _FakeSessionUseCase
) -> None:
    await fake_session_uc.create(title="Conversa sobre licitações")
    resp = await client.get("/api/sessions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["title"] == "Conversa sobre licitações"
