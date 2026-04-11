"""Unit tests for SessionService — Etapa 6 (TDD RED → GREEN)."""

from __future__ import annotations

import uuid

import pytest

from app.domain.entities.session import ChatSession
from app.domain.value_objects.chat_message import ChatMessage


class TestSessionService:
    @pytest.fixture
    def service(self, fake_session_repo):
        from app.domain.services.session_service import SessionService

        return SessionService(session_repo=fake_session_repo)

    # ------------------------------------------------------------------
    # create
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_create_returns_chat_session(self, service):
        session = await service.create()
        assert isinstance(session, ChatSession)

    @pytest.mark.asyncio
    async def test_create_with_title(self, service):
        session = await service.create(title="Minha sessão")
        assert session.title == "Minha sessão"

    @pytest.mark.asyncio
    async def test_create_without_title(self, service):
        session = await service.create()
        assert session.title is None

    @pytest.mark.asyncio
    async def test_create_assigns_uuid(self, service):
        session = await service.create()
        assert session.id is not None

    @pytest.mark.asyncio
    async def test_create_multiple_sessions_have_different_ids(self, service):
        s1 = await service.create()
        s2 = await service.create()
        assert s1.id != s2.id

    # ------------------------------------------------------------------
    # get
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_returns_existing_session(self, service):
        created = await service.create(title="Busca transparência")
        fetched = await service.get(created.id)
        assert fetched is not None
        assert fetched.id == created.id

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown(self, service):
        result = await service.get(uuid.uuid4())
        assert result is None

    # ------------------------------------------------------------------
    # list_messages
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_list_messages_empty_for_new_session(self, service):
        session = await service.create()
        messages = await service.list_messages(session.id)
        assert messages == []

    @pytest.mark.asyncio
    async def test_list_messages_returns_chat_messages(self, service, fake_session_repo):
        session = await service.create()
        msg = ChatMessage(
            role="user",
            content="Olá",
            sources=[],
            tables=[],
        )
        await fake_session_repo.save_message(session.id, msg)
        messages = await service.list_messages(session.id)
        assert len(messages) == 1
        assert messages[0].content == "Olá"

    @pytest.mark.asyncio
    async def test_list_messages_returns_empty_for_unknown_session(self, service):
        messages = await service.list_messages(uuid.uuid4())
        assert messages == []

    @pytest.mark.asyncio
    async def test_list_messages_preserves_order(self, service, fake_session_repo):
        session = await service.create()
        for i in range(3):
            msg = ChatMessage(role="user", content=f"msg {i}", sources=[], tables=[])
            await fake_session_repo.save_message(session.id, msg)
        messages = await service.list_messages(session.id)
        assert [m.content for m in messages] == ["msg 0", "msg 1", "msg 2"]
