"""Testes unitários: DataAgent — loop de function calling com mock do gateway."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.entities.document_table import DocumentTable
from app.domain.ports.outbound.function_calling_gateway import (
    FunctionCall,
    FunctionCallingResponse,
)
from app.domain.services.data_agent import AnalysisResult, DataAgent
from app.domain.services.tool_executor import ToolExecutor
from app.domain.value_objects.search_result import ChunkMatch, PageMatch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_table() -> DocumentTable:
    return DocumentTable(
        id=1,
        document_url="https://tre-pi.jus.br/contratos",
        table_index=0,
        headers=["Número", "Fornecedor", "Valor", "Status"],
        rows=[
            ["001/2025", "Empresa A", "R$ 150.000,00", "Vigente"],
            ["002/2025", "Empresa B", "R$ 250.000,00", "Vigente"],
            ["003/2025", "Empresa C", "R$ 100.000,00", "Encerrado"],
        ],
        caption="Contratos 2025",
    )


@pytest.fixture
def executor() -> ToolExecutor:
    return ToolExecutor()


def make_agent(responses: list[FunctionCallingResponse], max_rounds: int = 5) -> DataAgent:
    """Cria DataAgent com mock do gateway retornando responses em sequência."""
    llm = MagicMock()
    llm.generate_with_tools = AsyncMock(side_effect=responses)
    return DataAgent(llm=llm, tool_executor=ToolExecutor(), max_tool_rounds=max_rounds)


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------


class TestDataAgentAnalyze:
    @pytest.mark.asyncio
    async def test_single_tool_call_count_rows(self, sample_table: DocumentTable):
        """DataAgent deve invocar count_rows e retornar resultado correto."""
        responses = [
            # Rodada 1: LLM solicita count_rows
            FunctionCallingResponse(
                text=None,
                function_calls=[FunctionCall(name="count_rows", args={"table_index": 0})],
                finish_reason="function_call",
            ),
            # Rodada 2: LLM finaliza com data_summary
            FunctionCallingResponse(
                text='{"selected_table_indices": [0], "key_findings": "3 contratos", "query_answered": true}',
                function_calls=[],
                finish_reason="stop",
            ),
        ]
        agent = make_agent(responses)
        result = await agent.analyze(
            query="Quantos contratos existem?",
            pages=[],
            chunks=[],
            tables=[sample_table],
        )

        assert isinstance(result, AnalysisResult)
        assert len(result.computed_metrics) == 1
        assert result.computed_metrics[0].tool_name == "count_rows"
        assert result.computed_metrics[0].result == 3
        assert sample_table in result.selected_tables

    @pytest.mark.asyncio
    async def test_sum_column_tool_call(self, sample_table: DocumentTable):
        """DataAgent deve invocar sum_column e retornar soma correta."""
        responses = [
            FunctionCallingResponse(
                text=None,
                function_calls=[
                    FunctionCall(name="sum_column", args={"table_index": 0, "column": "Valor"})
                ],
                finish_reason="function_call",
            ),
            FunctionCallingResponse(
                text='{"selected_table_indices": [0], "key_findings": "total R$ 500.000", "query_answered": true}',
                function_calls=[],
                finish_reason="stop",
            ),
        ]
        agent = make_agent(responses)
        result = await agent.analyze(
            query="Qual o valor total dos contratos?",
            pages=[],
            chunks=[],
            tables=[sample_table],
        )

        assert len(result.computed_metrics) == 1
        assert result.computed_metrics[0].tool_name == "sum_column"
        assert result.computed_metrics[0].result == Decimal("500000.00")

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_in_sequence(self, sample_table: DocumentTable):
        """DataAgent deve executar múltiplas tools em rounds sequenciais."""
        responses = [
            FunctionCallingResponse(
                text=None,
                function_calls=[FunctionCall(name="count_rows", args={"table_index": 0})],
                finish_reason="function_call",
            ),
            FunctionCallingResponse(
                text=None,
                function_calls=[
                    FunctionCall(name="sum_column", args={"table_index": 0, "column": "Valor"})
                ],
                finish_reason="function_call",
            ),
            FunctionCallingResponse(
                text='{"selected_table_indices": [0], "query_answered": true}',
                function_calls=[],
                finish_reason="stop",
            ),
        ]
        agent = make_agent(responses)
        result = await agent.analyze(
            query="Quantos contratos e qual o valor total?",
            pages=[],
            chunks=[],
            tables=[sample_table],
        )

        assert len(result.computed_metrics) == 2
        tool_names = [m.tool_name for m in result.computed_metrics]
        assert "count_rows" in tool_names
        assert "sum_column" in tool_names

    @pytest.mark.asyncio
    async def test_max_rounds_respected(self, sample_table: DocumentTable):
        """DataAgent deve parar após max_tool_rounds, mesmo sem stop."""
        # Sempre retorna function_call — nunca para
        always_calls = FunctionCallingResponse(
            text=None,
            function_calls=[FunctionCall(name="count_rows", args={"table_index": 0})],
            finish_reason="function_call",
        )
        responses = [always_calls] * 10  # mais do que max_rounds

        agent = make_agent(responses, max_rounds=3)
        result = await agent.analyze(
            query="Loop infinito?",
            pages=[],
            chunks=[],
            tables=[sample_table],
        )

        # Deve ter parado em 3 rounds (max_rounds)
        assert len(result.tool_calls_log) == 3

    @pytest.mark.asyncio
    async def test_no_tables_returns_empty_metrics(self):
        """Sem tabelas, DataAgent deve retornar AnalysisResult sem métricas."""
        responses = [
            FunctionCallingResponse(
                text='{"query_answered": false, "key_findings": "sem dados tabulares"}',
                function_calls=[],
                finish_reason="stop",
            )
        ]
        agent = make_agent(responses)
        result = await agent.analyze(
            query="Qualquer coisa",
            pages=[],
            chunks=[],
            tables=[],
        )

        assert result.selected_tables == []
        assert result.computed_metrics == []

    @pytest.mark.asyncio
    async def test_relevant_chunks_sorted_by_score(self):
        """DataAgent deve retornar chunks ordenados por score."""
        from app.domain.entities.chunk import DocumentChunk

        def make_chunk(score: float, text: str) -> ChunkMatch:
            chunk = DocumentChunk(
                id=1,
                document_url="http://x",
                chunk_index=0,
                text=text,
                token_count=10,
            )
            return ChunkMatch(
                chunk=chunk,
                document_title="Doc",
                document_url="http://x",
                score=score,
            )

        chunks = [
            make_chunk(0.5, "chunk baixo"),
            make_chunk(0.9, "chunk alto"),
            make_chunk(0.7, "chunk médio"),
        ]

        responses = [
            FunctionCallingResponse(
                text='{"query_answered": true}',
                function_calls=[],
                finish_reason="stop",
            )
        ]
        agent = make_agent(responses)
        result = await agent.analyze(
            query="query",
            pages=[],
            chunks=chunks,
            tables=[],
        )

        assert "alto" in result.relevant_chunks[0].chunk.text
        assert "médio" in result.relevant_chunks[1].chunk.text

    @pytest.mark.asyncio
    async def test_tool_calls_logged(self, sample_table: DocumentTable):
        """Todas as tool calls devem ser registradas no log de auditoria."""
        responses = [
            FunctionCallingResponse(
                text=None,
                function_calls=[FunctionCall(name="count_rows", args={"table_index": 0})],
                finish_reason="function_call",
            ),
            FunctionCallingResponse(
                text='{"query_answered": true}',
                function_calls=[],
                finish_reason="stop",
            ),
        ]
        agent = make_agent(responses)
        result = await agent.analyze("q", [], [], [sample_table])

        assert len(result.tool_calls_log) == 1
        log = result.tool_calls_log[0]
        assert log["tool"] == "count_rows"
        assert log["round"] == 0
        assert log["result"] == 3
