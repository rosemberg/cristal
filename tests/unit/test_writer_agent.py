"""Testes unitários: WriterAgent — redação a partir de dados pré-computados."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document_table import DocumentTable
from app.domain.services.data_agent import AnalysisResult
from app.domain.services.tool_executor import ToolResult
from app.domain.services.writer_agent import WriterAgent
from app.domain.value_objects.search_result import ChunkMatch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_chunk_match(text: str, url: str = "https://tre-pi.jus.br/doc", title: str = "Documento") -> ChunkMatch:
    return ChunkMatch(
        chunk=DocumentChunk(id=1, document_url=url, chunk_index=0, text=text, token_count=len(text.split())),
        document_title=title,
        document_url=url,
        score=1.0,
    )


def make_analysis(
    summary: str = '{"query_answered": true, "key_findings": "3 contratos vigentes"}',
    metrics: list[ToolResult] | None = None,
    tables: list[DocumentTable] | None = None,
    chunks: list[str] | None = None,
) -> AnalysisResult:
    chunk_matches = [make_chunk_match(c) for c in (chunks or [])]
    return AnalysisResult(
        selected_tables=tables or [],
        computed_metrics=metrics or [],
        relevant_chunks=chunk_matches,
        data_summary=summary,
    )


def make_writer(response_text: str) -> WriterAgent:
    llm = MagicMock()
    llm.generate = AsyncMock(return_value=response_text)
    return WriterAgent(llm=llm)


# ---------------------------------------------------------------------------
# Testes
# ---------------------------------------------------------------------------


class TestWriterAgent:
    @pytest.mark.asyncio
    async def test_write_returns_llm_text(self):
        """WriterAgent deve retornar o texto gerado pelo LLM."""
        writer = make_writer("Foram encontrados **3 contratos** vigentes.")
        analysis = make_analysis()

        result = await writer.write("Quantos contratos vigentes?", analysis)

        assert result == "Foram encontrados **3 contratos** vigentes."

    @pytest.mark.asyncio
    async def test_write_passes_metrics_in_prompt(self):
        """WriterAgent deve incluir métricas calculadas no prompt enviado ao LLM."""
        llm = MagicMock()
        captured_messages: list = []

        async def capture_generate(system_prompt, messages, temperature=0.3):
            captured_messages.extend(messages)
            return "Resposta."

        llm.generate = capture_generate
        writer = WriterAgent(llm=llm)

        metrics = [
            ToolResult(tool_name="count_rows", result=51, metadata={}),
            ToolResult(tool_name="sum_column", result=Decimal("1234567.89"), metadata={"column": "Valor"}),
        ]
        analysis = make_analysis(metrics=metrics)

        await writer.write("Quantos contratos?", analysis)

        # Verifica que o prompt contém os dados calculados
        user_content = captured_messages[0]["content"]
        assert "count_rows" in user_content
        assert "sum_column" in user_content
        assert "51" in user_content

    @pytest.mark.asyncio
    async def test_write_strips_whitespace(self):
        """WriterAgent deve remover espaços extras da resposta."""
        writer = make_writer("  Texto com espaços.  \n\n")
        analysis = make_analysis()

        result = await writer.write("query", analysis)

        assert result == "Texto com espaços."

    @pytest.mark.asyncio
    async def test_write_fallback_on_llm_error(self):
        """WriterAgent deve usar fallback quando o LLM lança exceção."""
        llm = MagicMock()
        llm.generate = AsyncMock(side_effect=RuntimeError("LLM indisponível"))
        writer = WriterAgent(llm=llm)

        metrics = [ToolResult(tool_name="count_rows", result=5, metadata={})]
        analysis = make_analysis(metrics=metrics)

        result = await writer.write("Quantos?", analysis)

        # Fallback deve mencionar a query e os dados
        assert "Quantos?" in result
        assert "count_rows" in result or "5" in result

    @pytest.mark.asyncio
    async def test_write_passes_relevant_chunks(self):
        """WriterAgent deve incluir chunks relevantes no contexto enviado ao LLM."""
        llm = MagicMock()
        captured: list = []

        async def capture_generate(system_prompt, messages, temperature=0.3):
            captured.extend(messages)
            return "Ok."

        llm.generate = capture_generate
        writer = WriterAgent(llm=llm)

        analysis = make_analysis(chunks=["Trecho importante sobre contratos do TRE-PI."])

        await writer.write("query", analysis)

        user_content = captured[0]["content"]
        assert "Trecho importante" in user_content

    @pytest.mark.asyncio
    async def test_write_uses_low_temperature(self):
        """WriterAgent deve chamar LLM com temperature específica do writer."""
        llm = MagicMock()
        captured_kwargs: dict = {}

        async def capture_generate(system_prompt, messages, temperature=0.3):
            captured_kwargs["temperature"] = temperature
            return "Resposta."

        llm.generate = capture_generate
        writer = WriterAgent(llm=llm)

        await writer.write("query", make_analysis())

        assert captured_kwargs["temperature"] == 0.3
