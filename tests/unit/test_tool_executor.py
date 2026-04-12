"""Testes unitários: ToolExecutor — aritmética determinística."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.services.tool_executor import ToolExecutor, ToolResult


@pytest.fixture
def executor() -> ToolExecutor:
    return ToolExecutor()


@pytest.fixture
def contratos_table() -> dict:
    return {
        "headers": ["Número", "Fornecedor", "Valor", "Status"],
        "rows": [
            ["001/2025", "Empresa A", "R$ 150.000,00", "Vigente"],
            ["002/2025", "Empresa B", "R$ 250.000,00", "Vigente"],
            ["003/2025", "Empresa C", "R$ 100.000,00", "Encerrado"],
            ["004/2025", "Empresa D", "R$ 500.000,00", "Vigente"],
            ["TOTAL", "", "R$ 1.000.000,00", ""],
        ],
    }


@pytest.fixture
def empty_table() -> dict:
    return {"headers": ["Nome", "Valor"], "rows": []}


# ==========================================================================
# count_rows
# ==========================================================================


class TestCountRows:
    def test_count_excludes_total_row(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.count_rows(contratos_table)
        assert result.result == 4

    def test_count_empty_table(self, executor: ToolExecutor, empty_table: dict):
        result = executor.count_rows(empty_table)
        assert result.result == 0

    def test_count_with_filter(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.count_rows(contratos_table, column="Status", value="Vigente")
        assert result.result == 3

    def test_count_with_filter_no_match(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.count_rows(contratos_table, column="Status", value="Suspenso")
        assert result.result == 0

    def test_count_filter_column_not_found(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.count_rows(contratos_table, column="ColunaNaoExiste", value="x")
        assert result.result == 0
        assert "error" in result.metadata

    def test_count_subtotal_row_excluded(self, executor: ToolExecutor):
        table = {
            "headers": ["Item"],
            "rows": [["A"], ["B"], ["SUBTOTAL"], ["C"]],
        }
        result = executor.count_rows(table)
        assert result.result == 3

    def test_count_grand_total_excluded(self, executor: ToolExecutor):
        table = {
            "headers": ["Item"],
            "rows": [["A"], ["GRAND TOTAL"]],
        }
        result = executor.count_rows(table)
        assert result.result == 1


# ==========================================================================
# sum_column
# ==========================================================================


class TestSumColumn:
    def test_sum_brl_format(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.sum_column(contratos_table, column="Valor")
        # 150000 + 250000 + 100000 + 500000 (exclui TOTAL)
        assert result.result == Decimal("1000000.00")

    def test_sum_column_not_found(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.sum_column(contratos_table, column="ColunaNaoExiste")
        assert result.result == Decimal(0)
        assert "error" in result.metadata

    def test_sum_empty_table(self, executor: ToolExecutor, empty_table: dict):
        result = executor.sum_column(empty_table, column="Valor")
        assert result.result == Decimal(0)

    def test_sum_skips_non_numeric(self, executor: ToolExecutor):
        table = {
            "headers": ["Item", "Valor"],
            "rows": [
                ["A", "R$ 1.234,56"],
                ["B", "—"],
                ["C", "N/A"],
                ["D", "R$ 2.345,67"],
            ],
        }
        result = executor.sum_column(table, column="Valor")
        assert result.result == Decimal("3580.23")

    def test_sum_decimal_format(self, executor: ToolExecutor):
        table = {
            "headers": ["Nome", "Valor"],
            "rows": [
                ["A", "1234.56"],
                ["B", "5678.90"],
            ],
        }
        result = executor.sum_column(table, column="Valor")
        assert result.result == Decimal("6913.46")

    def test_sum_excludes_total(self, executor: ToolExecutor):
        table = {
            "headers": ["Item", "Valor"],
            "rows": [
                ["A", "100"],
                ["B", "200"],
                ["TOTAL", "300"],  # deve ser excluído
            ],
        }
        result = executor.sum_column(table, column="Valor")
        assert result.result == Decimal("300")
        assert result.metadata["summed_rows"] == 2


# ==========================================================================
# filter_rows
# ==========================================================================


class TestFilterRows:
    def test_filter_eq_string(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.filter_rows(contratos_table, column="Status", operator="eq", value="Vigente")
        assert isinstance(result.result, list)
        assert len(result.result) == 3

    def test_filter_contains(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.filter_rows(contratos_table, column="Fornecedor", operator="contains", value="Empresa")
        assert len(result.result) == 4

    def test_filter_numeric_gt(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.filter_rows(contratos_table, column="Valor", operator="gt", value="200000")
        # 250000 e 500000 → 2 linhas
        assert len(result.result) == 2

    def test_filter_numeric_lte(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.filter_rows(contratos_table, column="Valor", operator="lte", value="150000")
        # 150000 e 100000 → 2 linhas
        assert len(result.result) == 2

    def test_filter_column_not_found(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.filter_rows(contratos_table, column="X", operator="eq", value="Y")
        assert result.result == []
        assert "error" in result.metadata

    def test_filter_startswith(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.filter_rows(contratos_table, column="Número", operator="startswith", value="00")
        assert len(result.result) == 4

    def test_filter_excludes_total(self, executor: ToolExecutor, contratos_table: dict):
        # TOTAL row nunca deve aparecer nos resultados
        result = executor.filter_rows(contratos_table, column="Número", operator="contains", value="")
        for row in result.result:
            assert str(row[0]).upper() not in {"TOTAL", "SUBTOTAL"}

    def test_filter_neq(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.filter_rows(contratos_table, column="Status", operator="neq", value="Encerrado")
        assert len(result.result) == 3


# ==========================================================================
# sort_rows
# ==========================================================================


class TestSortRows:
    def test_sort_numeric_desc(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.sort_rows(contratos_table, column="Valor", order="desc")
        rows = result.result
        assert isinstance(rows, list)
        # Primeiro deve ser 500000
        assert "500" in str(rows[0][2])

    def test_sort_numeric_asc(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.sort_rows(contratos_table, column="Valor", order="asc")
        rows = result.result
        # Primeiro deve ser 100000
        assert "100" in str(rows[0][2])

    def test_sort_column_not_found(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.sort_rows(contratos_table, column="ColunaNaoExiste")
        assert "error" in result.metadata

    def test_sort_excludes_total(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.sort_rows(contratos_table, column="Valor")
        for row in result.result:
            assert str(row[0]).upper() not in {"TOTAL", "SUBTOTAL"}

    def test_sort_string_column(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.sort_rows(contratos_table, column="Fornecedor", order="asc")
        nomes = [row[1] for row in result.result]
        assert nomes == sorted(nomes, key=str.lower)


# ==========================================================================
# extract_value
# ==========================================================================


class TestExtractValue:
    def test_extract_value_found(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.extract_value(
            contratos_table,
            filter_column="Fornecedor",
            filter_value="Empresa A",
            target_column="Valor",
        )
        assert result.result == "R$ 150.000,00"

    def test_extract_value_partial_match(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.extract_value(
            contratos_table,
            filter_column="Fornecedor",
            filter_value="empresa b",  # case-insensitive partial match
            target_column="Número",
        )
        assert result.result == "002/2025"

    def test_extract_value_not_found(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.extract_value(
            contratos_table,
            filter_column="Fornecedor",
            filter_value="Empresa Z",
            target_column="Valor",
        )
        assert result.result is None
        assert "error" in result.metadata

    def test_extract_filter_column_not_found(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.extract_value(
            contratos_table,
            filter_column="ColunaNaoExiste",
            filter_value="x",
            target_column="Valor",
        )
        assert result.result is None
        assert "error" in result.metadata

    def test_extract_target_column_not_found(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.extract_value(
            contratos_table,
            filter_column="Fornecedor",
            filter_value="Empresa A",
            target_column="ColunaNaoExiste",
        )
        assert result.result is None
        assert "error" in result.metadata


# ==========================================================================
# execute (dispatcher)
# ==========================================================================


class TestExecuteDispatcher:
    def test_dispatch_count_rows(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.execute("count_rows", {"table_index": 0}, [contratos_table])
        assert result.tool_name == "count_rows"
        assert result.result == 4

    def test_dispatch_sum_column(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.execute(
            "sum_column",
            {"table_index": 0, "column": "Valor"},
            [contratos_table],
        )
        assert result.result == Decimal("1000000.00")

    def test_dispatch_unknown_tool(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.execute("unknown_tool", {"table_index": 0}, [contratos_table])
        assert result.result is None
        assert "error" in result.metadata

    def test_dispatch_invalid_table_index(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.execute("count_rows", {"table_index": 99}, [contratos_table])
        assert result.result is None
        assert "error" in result.metadata

    def test_dispatch_negative_table_index(self, executor: ToolExecutor, contratos_table: dict):
        result = executor.execute("count_rows", {"table_index": -1}, [contratos_table])
        assert result.result is None
        assert "error" in result.metadata
