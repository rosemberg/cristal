"""Testes unitários: MultiAgentChatService — orquestração do pipeline."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from app.domain.entities.document_table import DocumentTable
from app.domain.services.data_agent import AnalysisResult, DataAgent
from app.domain.services.multi_agent_chat_service import MultiAgentChatService
from app.domain.services.response_assembler import ResponseAssembler
from app.domain.services.tool_executor import ToolResult
from app.domain.services.writer_agent import WriterAgent
from app.domain.value_objects.chat_message import ChatMessage
from app.domain.value_objects.progress_event import ProgressEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_service(
    search_results: tuple | None = None,
    analysis: AnalysisResult | None = None,
    narrative: str = "Texto gerado.",
    chat_message: ChatMessage | None = None,
) -> MultiAgentChatService:
    """Cria MultiAgentChatService com todos os colaboradores mockados."""
    # SearchRepository mock
    search_repo = MagicMock()
    search_repo.search_pages = AsyncMock(return_value=[])
    search_repo.search_chunks = AsyncMock(return_value=[])
    search_repo.search_tables = AsyncMock(return_value=[])

    # SessionRepository mock
    session_repo = MagicMock()
    session_repo.get = AsyncMock(return_value=None)
    session_repo.save_message = AsyncMock()

    # AnalyticsRepository mock
    analytics_repo = MagicMock()
    analytics_repo.log_query = AsyncMock()

    # DataAgent mock
    default_analysis = AnalysisResult(
        selected_tables=[],
        computed_metrics=[],
        relevant_chunks=[],
        data_summary='{"query_answered": true}',
    )
    data_agent = MagicMock(spec=DataAgent)
    data_agent.analyze = AsyncMock(return_value=analysis or default_analysis)

    # WriterAgent mock
    writer_agent = MagicMock(spec=WriterAgent)
    writer_agent.write = AsyncMock(return_value=narrative)

    # ResponseAssembler mock
    default_chat_message = ChatMessage(
        role="assistant",
        content=narrative,
        sources=[],
        tables=[],
        suggestions=[],
        metrics=[],
    )
    assembler = MagicMock(spec=ResponseAssembler)
    assembler.assemble = MagicMock(return_value=chat_message or default_chat_message)

    # TableValidatorAgent mock
    table_validator = MagicMock()
    table_validator.select_best_tables = MagicMock(side_effect=lambda x: x)

    return MultiAgentChatService(
        search_repo=search_repo,
        session_repo=session_repo,
        analytics_repo=analytics_repo,
        data_agent=data_agent,
        writer_agent=writer_agent,
        assembler=assembler,
        table_validator=table_validator,
    )


# ---------------------------------------------------------------------------
# process_message (endpoint JSON)
# ---------------------------------------------------------------------------


class TestProcessMessage:
    @pytest.mark.asyncio
    async def test_returns_chat_message(self):
        service = make_service()
        result = await service.process_message("Quantos contratos?")
        assert isinstance(result, ChatMessage)
        assert result.role == "assistant"

    @pytest.mark.asyncio
    async def test_calls_data_agent(self):
        service = make_service()
        await service.process_message("query")
        service._data_agent.analyze.assert_called_once()

    @pytest.mark.asyncio
    async def test_calls_writer_agent(self):
        service = make_service()
        await service.process_message("query")
        service._writer_agent.write.assert_called_once()

    @pytest.mark.asyncio
    async def test_calls_assembler(self):
        service = make_service()
        await service.process_message("query")
        service._assembler.assemble.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_analytics(self):
        service = make_service()
        await service.process_message("query")
        service._analytics.log_query.assert_called_once()

    @pytest.mark.asyncio
    async def test_does_not_persist_without_session(self):
        service = make_service()
        await service.process_message("query", session_id=None)
        service._sessions.save_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_persists_with_valid_session(self):
        service = make_service()
        session_id = uuid4()
        # Sessão existe
        mock_session = MagicMock()
        service._sessions.get = AsyncMock(return_value=mock_session)

        await service.process_message("query", session_id=session_id)
        assert service._sessions.save_message.call_count == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_returns_content_from_assembler(self):
        msg = ChatMessage(
            role="assistant",
            content="Conteúdo específico.",
            sources=[],
            tables=[],
        )
        service = make_service(chat_message=msg)
        result = await service.process_message("query")
        assert result.content == "Conteúdo específico."


# ---------------------------------------------------------------------------
# process_message_stream (SSE)
# ---------------------------------------------------------------------------


class TestProcessMessageStream:
    @pytest.mark.asyncio
    async def test_emits_searching_event(self):
        service = make_service()
        events: list[ProgressEvent] = []
        async for event in service.process_message_stream("query"):
            events.append(event)

        event_types = [e.event_type for e in events]
        assert "searching" in event_types

    @pytest.mark.asyncio
    async def test_emits_analyzing_event(self):
        service = make_service()
        events: list[ProgressEvent] = []
        async for event in service.process_message_stream("query"):
            events.append(event)

        event_types = [e.event_type for e in events]
        assert "analyzing" in event_types

    @pytest.mark.asyncio
    async def test_emits_writing_event(self):
        service = make_service()
        events: list[ProgressEvent] = []
        async for event in service.process_message_stream("query"):
            events.append(event)

        event_types = [e.event_type for e in events]
        assert "writing" in event_types

    @pytest.mark.asyncio
    async def test_last_event_is_done(self):
        service = make_service()
        events: list[ProgressEvent] = []
        async for event in service.process_message_stream("query"):
            events.append(event)

        assert events[-1].event_type == "done"

    @pytest.mark.asyncio
    async def test_done_event_has_text(self):
        service = make_service(narrative="Resposta final.")
        events: list[ProgressEvent] = []
        async for event in service.process_message_stream("query"):
            events.append(event)

        done_event = next(e for e in events if e.event_type == "done")
        assert done_event.data.get("text") == "Resposta final."

    @pytest.mark.asyncio
    async def test_emits_tool_call_events(self):
        analysis = AnalysisResult(
            selected_tables=[],
            computed_metrics=[],
            relevant_chunks=[],
            data_summary='{"query_answered": true}',
            tool_calls_log=[
                {"round": 0, "tool": "count_rows", "args": {"table_index": 0}, "result": 42, "metadata": {}},
            ],
        )
        service = make_service(analysis=analysis)
        events: list[ProgressEvent] = []
        async for event in service.process_message_stream("query"):
            events.append(event)

        tool_events = [e for e in events if e.event_type == "tool_call"]
        assert len(tool_events) == 1
        assert tool_events[0].data["tool"] == "count_rows"

    @pytest.mark.asyncio
    async def test_emits_error_event_on_failure(self):
        service = make_service()
        service._data_agent.analyze = AsyncMock(side_effect=RuntimeError("LLM down"))

        events: list[ProgressEvent] = []
        async for event in service.process_message_stream("query"):
            events.append(event)

        error_events = [e for e in events if e.event_type == "error"]
        assert len(error_events) == 1

    @pytest.mark.asyncio
    async def test_event_sequence_order(self):
        service = make_service()
        events: list[ProgressEvent] = []
        async for event in service.process_message_stream("query"):
            events.append(event)

        event_types = [e.event_type for e in events]
        # Ordem esperada: searching → analyzing → writing → done
        searching_idx = event_types.index("searching")
        analyzing_idx = event_types.index("analyzing")
        writing_idx = event_types.index("writing")
        done_idx = event_types.index("done")

        assert searching_idx < analyzing_idx < writing_idx < done_idx


# ---------------------------------------------------------------------------
# get_suggestions
# ---------------------------------------------------------------------------


class TestGetSuggestions:
    @pytest.mark.asyncio
    async def test_returns_list_of_strings(self):
        service = make_service()
        result = await service.get_suggestions()
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)
        assert len(result) > 0
