"""Testes unitários: ResponseAssembler — fonte única de verdade para ChatMessage."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document_table import DocumentTable
from app.domain.services.data_agent import AnalysisResult
from app.domain.services.response_assembler import ResponseAssembler
from app.domain.services.tool_executor import ToolResult
from app.domain.value_objects.chat_message import ChatMessage
from app.domain.value_objects.search_result import ChunkMatch


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def assembler() -> ResponseAssembler:
    return ResponseAssembler()


@pytest.fixture
def contratos_table() -> DocumentTable:
    return DocumentTable(
        id=1,
        document_url="https://tre-pi.jus.br/contratos",
        table_index=0,
        headers=["Número", "Fornecedor", "Valor"],
        rows=[
            ["001/2025", "Empresa A", "R$ 150.000,00"],
            ["002/2025", "Empresa B", "R$ 250.000,00"],
            ["003/2025", "Empresa C", "R$ 100.000,00"],
        ],
        caption="Contratos 2025",
    )


def make_chunk_match(text: str, url: str = "https://tre-pi.jus.br/doc", title: str = "Documento") -> ChunkMatch:
    return ChunkMatch(
        chunk=DocumentChunk(id=1, document_url=url, chunk_index=0, text=text, token_count=len(text.split())),
        document_title=title,
        document_url=url,
        score=1.0,
    )


def make_analysis(
    tables: list[DocumentTable] | None = None,
    metrics: list[ToolResult] | None = None,
    chunks: list[str] | None = None,
    summary: str = '{"query_answered": true}',
) -> AnalysisResult:
    chunk_matches = [make_chunk_match(c) for c in (chunks or [])]
    return AnalysisResult(
        selected_tables=tables or [],
        computed_metrics=metrics or [],
        relevant_chunks=chunk_matches,
        data_summary=summary,
    )


# ---------------------------------------------------------------------------
# assemble — geral
# ---------------------------------------------------------------------------


class TestAssemble:
    def test_returns_chat_message(self, assembler: ResponseAssembler, contratos_table: DocumentTable):
        analysis = make_analysis(tables=[contratos_table])
        result = assembler.assemble("query", "Texto narrativo.", analysis)
        assert isinstance(result, ChatMessage)
        assert result.role == "assistant"

    def test_narrative_preserved(self, assembler: ResponseAssembler):
        analysis = make_analysis()
        result = assembler.assemble("query", "Texto **importante**.", analysis)
        assert "Texto **importante**" in result.content

    def test_empty_analysis_returns_valid_message(self, assembler: ResponseAssembler):
        analysis = make_analysis()
        result = assembler.assemble("query", "Sem dados.", analysis)
        assert result.content == "Sem dados."
        assert result.tables == []
        assert result.metrics == []


# ---------------------------------------------------------------------------
# _build_tables — TOTAL único
# ---------------------------------------------------------------------------


class TestBuildTables:
    def test_total_row_computed_once(self, assembler: ResponseAssembler, contratos_table: DocumentTable):
        """Deve haver exatamente uma linha TOTAL por tabela."""
        analysis = make_analysis(tables=[contratos_table])
        result = assembler.assemble("query", "texto", analysis)

        assert len(result.tables) == 1
        total_rows = [r for r in result.tables[0].rows if r[0].upper() == "TOTAL"]
        assert len(total_rows) == 1

    def test_total_value_correct(self, assembler: ResponseAssembler, contratos_table: DocumentTable):
        """TOTAL deve ser a soma exata das linhas de dados."""
        analysis = make_analysis(tables=[contratos_table])
        result = assembler.assemble("query", "texto", analysis)

        total_row = next(r for r in result.tables[0].rows if r[0].upper() == "TOTAL")
        # 150000 + 250000 + 100000 = 500000
        assert "500.000" in total_row[2]

    def test_existing_total_row_not_duplicated(self, assembler: ResponseAssembler):
        """Tabela que já tem linha TOTAL não deve ter dois TOTAIs."""
        table = DocumentTable(
            id=2,
            document_url="https://x",
            table_index=0,
            headers=["Item", "Valor"],
            rows=[
                ["A", "R$ 100,00"],
                ["B", "R$ 200,00"],
                ["TOTAL", "R$ 300,00"],  # já existe
            ],
        )
        analysis = make_analysis(tables=[table])
        result = assembler.assemble("query", "texto", analysis)

        total_rows = [r for r in result.tables[0].rows if r[0].upper() == "TOTAL"]
        assert len(total_rows) == 1

    def test_table_without_monetary_column_no_total(self, assembler: ResponseAssembler):
        """Tabela sem coluna monetária não deve ter linha TOTAL."""
        table = DocumentTable(
            id=3,
            document_url="https://x",
            table_index=0,
            headers=["Nome", "Cargo", "Departamento"],
            rows=[
                ["João", "Analista", "TI"],
                ["Maria", "Técnica", "RH"],
            ],
        )
        analysis = make_analysis(tables=[table])
        result = assembler.assemble("query", "texto", analysis)

        total_rows = [r for r in result.tables[0].rows if r[0].upper() == "TOTAL"]
        assert len(total_rows) == 0


# ---------------------------------------------------------------------------
# _build_metrics — fonte única das ToolResults
# ---------------------------------------------------------------------------


class TestBuildMetrics:
    def test_count_metric(self, assembler: ResponseAssembler):
        metrics = [ToolResult(tool_name="count_rows", result=51, metadata={})]
        analysis = make_analysis(metrics=metrics)
        result = assembler.assemble("query", "texto", analysis)

        assert len(result.metrics) == 1
        assert result.metrics[0].value == "51"
        assert "registros" in result.metrics[0].label.lower()

    def test_sum_metric_formatted_brl(self, assembler: ResponseAssembler):
        metrics = [
            ToolResult(
                tool_name="sum_column",
                result=Decimal("1234567.89"),
                metadata={"column": "Valor"},
            )
        ]
        analysis = make_analysis(metrics=metrics)
        result = assembler.assemble("query", "texto", analysis)

        assert result.metrics[0].value == "R$ 1.234.567,89"

    def test_duplicate_metrics_deduplicated(self, assembler: ResponseAssembler):
        """Métricas com mesmo label não devem aparecer duplicadas."""
        metrics = [
            ToolResult(tool_name="count_rows", result=10, metadata={}),
            ToolResult(tool_name="count_rows", result=10, metadata={}),
        ]
        analysis = make_analysis(metrics=metrics)
        result = assembler.assemble("query", "texto", analysis)

        count_metrics = [m for m in result.metrics if "registros" in m.label.lower()]
        assert len(count_metrics) == 1

    def test_error_metrics_excluded(self, assembler: ResponseAssembler):
        """ToolResults com erro não devem virar MetricItem."""
        metrics = [
            ToolResult(
                tool_name="sum_column",
                result=None,
                metadata={"error": "coluna não encontrada"},
            )
        ]
        analysis = make_analysis(metrics=metrics)
        result = assembler.assemble("query", "texto", analysis)
        assert result.metrics == []

    def test_list_result_excluded(self, assembler: ResponseAssembler):
        """ToolResults de filter/sort (lista) não devem virar MetricItem."""
        metrics = [
            ToolResult(
                tool_name="filter_rows",
                result=[["A", "100"], ["B", "200"]],
                metadata={"column": "Status", "operator": "eq", "value": "Vigente", "count": 2},
            )
        ]
        analysis = make_analysis(metrics=metrics)
        result = assembler.assemble("query", "texto", analysis)
        assert result.metrics == []


# ---------------------------------------------------------------------------
# _build_citations
# ---------------------------------------------------------------------------


class TestBuildCitations:
    def test_citations_from_tables(self, assembler: ResponseAssembler, contratos_table: DocumentTable):
        analysis = make_analysis(tables=[contratos_table])
        result = assembler.assemble("query", "texto", analysis)

        assert any("tre-pi.jus.br/contratos" in c.document_url for c in result.sources)

    def test_no_duplicate_citations(self, assembler: ResponseAssembler, contratos_table: DocumentTable):
        """Duas tabelas do mesmo URL não devem gerar citações duplicadas."""
        table2 = DocumentTable(
            id=2,
            document_url=contratos_table.document_url,  # mesmo URL
            table_index=1,
            headers=contratos_table.headers,
            rows=contratos_table.rows,
        )
        analysis = make_analysis(tables=[contratos_table, table2])
        result = assembler.assemble("query", "texto", analysis)

        urls = [c.document_url for c in result.sources if c.document_url]
        assert len(urls) == len(set(urls))


# ---------------------------------------------------------------------------
# _sanitize_narrative — remoção de tabelas duplicadas no texto
# ---------------------------------------------------------------------------


class TestSanitizeNarrative:
    def test_removes_markdown_table(self, assembler: ResponseAssembler, contratos_table: DocumentTable):
        """Tabela Markdown no texto deve ser removida."""
        text_with_table = (
            "Aqui estão os contratos:\n\n"
            "| Número | Fornecedor | Valor |\n"
            "|--------|------------|-------|\n"
            "| 001 | A | R$ 100 |\n\n"
            "Fim do texto."
        )
        analysis = make_analysis(tables=[contratos_table])
        result = assembler.assemble("query", text_with_table, analysis)

        assert "|--------|" not in result.content

    def test_plain_text_preserved(self, assembler: ResponseAssembler, contratos_table: DocumentTable):
        """Texto sem tabela não deve ser modificado."""
        plain_text = "Foram encontrados **3 contratos** vigentes em 2025."
        analysis = make_analysis(tables=[contratos_table])
        result = assembler.assemble("query", plain_text, analysis)

        assert "3 contratos" in result.content
