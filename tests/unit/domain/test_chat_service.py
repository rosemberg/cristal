"""Unit tests for ChatService — Etapa 6 (TDD RED → GREEN)."""

from __future__ import annotations

import json
import uuid

import pytest

from app.domain.value_objects.chat_message import ChatMessage
from tests.conftest import (
    FakeAnalyticsRepository,
    FakeLLMGateway,
    FakeSearchRepository,
    FakeSessionRepository,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_llm_response(
    text="Resposta do assistente.",
    sources=None,
    tables=None,
    suggestions=None,
) -> str:
    return json.dumps(
        {
            "text": text,
            "sources": sources or [],
            "tables": tables or [],
            "suggestions": suggestions or ["Saiba mais"],
        }
    )


def make_service(pages=None, llm_response=None, session_repo=None, analytics_repo=None):
    from app.domain.services.chat_service import ChatService

    search_repo = FakeSearchRepository(pages=pages or [])
    llm = FakeLLMGateway(response=llm_response or make_llm_response())
    session_r = session_repo or FakeSessionRepository()
    analytics_r = analytics_repo or FakeAnalyticsRepository()
    return ChatService(
        search_repo=search_repo,
        session_repo=session_r,
        analytics_repo=analytics_r,
        llm=llm,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestChatService:

    # ------------------------------------------------------------------
    # process_message — basic
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_process_message_returns_chat_message(self):
        service = make_service()
        result = await service.process_message("Olá, preciso de ajuda")
        assert isinstance(result, ChatMessage)

    @pytest.mark.asyncio
    async def test_process_message_role_is_assistant(self):
        service = make_service()
        result = await service.process_message("Olá")
        assert result.role == "assistant"

    @pytest.mark.asyncio
    async def test_process_message_content_non_empty(self):
        service = make_service()
        result = await service.process_message("consulta de licitação")
        assert isinstance(result.content, str)
        assert len(result.content) > 0

    # ------------------------------------------------------------------
    # process_message — LLM interaction
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_process_message_uses_llm_text(self):
        llm_response = make_llm_response(text="Texto específico da resposta.")
        service = make_service(llm_response=llm_response)
        result = await service.process_message("pergunta qualquer")
        assert result.content == "Texto específico da resposta."

    @pytest.mark.asyncio
    async def test_process_message_parses_sources(self):
        sources = [
            {
                "document_title": "Relatório 2023",
                "document_url": "https://tre-pi.jus.br/relatorio.pdf",
                "snippet": "Trecho relevante",
                "page_number": 3,
            }
        ]
        llm_response = make_llm_response(sources=sources)
        service = make_service(llm_response=llm_response)
        result = await service.process_message("relatório anual")
        assert len(result.sources) == 1
        assert result.sources[0].document_title == "Relatório 2023"

    @pytest.mark.asyncio
    async def test_process_message_parses_tables(self):
        tables = [
            {
                "headers": ["Coluna A", "Coluna B"],
                "rows": [["v1", "v2"]],
                "source_document": "https://tre-pi.jus.br/doc.pdf",
                "title": "Tabela salarial",
            }
        ]
        llm_response = make_llm_response(tables=tables)
        service = make_service(llm_response=llm_response)
        result = await service.process_message("tabela de servidores")
        assert len(result.tables) == 1
        assert result.tables[0].headers == ["Coluna A", "Coluna B"]

    @pytest.mark.asyncio
    async def test_process_message_malformed_json_fallback(self):
        """Resposta malformada deve retornar ChatMessage com texto bruto."""
        service = make_service(llm_response="Resposta em texto puro sem JSON")
        result = await service.process_message("qualquer coisa")
        assert isinstance(result, ChatMessage)
        assert len(result.content) > 0

    # ------------------------------------------------------------------
    # process_message — session handling
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_process_message_with_session_saves_message(self):
        session_repo = FakeSessionRepository()
        session = await session_repo.create(title="Sessão teste")
        service = make_service(session_repo=session_repo)

        await service.process_message("msg com sessão", session_id=session.id)

        stored = await session_repo.get(session.id)
        # deve ter pelo menos 1 mensagem salva
        assert len(stored.messages) >= 1

    @pytest.mark.asyncio
    async def test_process_message_without_session_does_not_create_session(self):
        session_repo = FakeSessionRepository()
        service = make_service(session_repo=session_repo)

        await service.process_message("msg sem sessão")

        # nenhuma sessão deve ter sido criada implicitamente
        sessions = await session_repo.list_sessions()
        assert sessions == []

    @pytest.mark.asyncio
    async def test_process_message_with_history(self):
        history = [{"role": "user", "content": "contexto anterior"}]
        service = make_service()
        result = await service.process_message("followup", history=history)
        assert isinstance(result, ChatMessage)

    # ------------------------------------------------------------------
    # process_message — analytics
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_process_message_logs_analytics(self):
        analytics_repo = FakeAnalyticsRepository()
        service = make_service(analytics_repo=analytics_repo)

        await service.process_message("consulta com analytics")

        assert len(analytics_repo._queries) == 1

    @pytest.mark.asyncio
    async def test_process_message_analytics_contains_query(self):
        analytics_repo = FakeAnalyticsRepository()
        service = make_service(analytics_repo=analytics_repo)

        await service.process_message("busca de licitação")

        logged = analytics_repo._queries[0]
        assert logged["query"] == "busca de licitação"

    @pytest.mark.asyncio
    async def test_process_message_analytics_has_response_time(self):
        analytics_repo = FakeAnalyticsRepository()
        service = make_service(analytics_repo=analytics_repo)

        await service.process_message("consulta qualquer")

        logged = analytics_repo._queries[0]
        assert isinstance(logged["response_time_ms"], int)
        assert logged["response_time_ms"] >= 0

    # ------------------------------------------------------------------
    # get_suggestions
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_get_suggestions_returns_list(self):
        service = make_service()
        suggestions = await service.get_suggestions()
        assert isinstance(suggestions, list)

    @pytest.mark.asyncio
    async def test_get_suggestions_returns_strings(self):
        service = make_service()
        suggestions = await service.get_suggestions()
        assert all(isinstance(s, str) for s in suggestions)

    @pytest.mark.asyncio
    async def test_get_suggestions_non_empty(self):
        service = make_service()
        suggestions = await service.get_suggestions()
        assert len(suggestions) > 0
