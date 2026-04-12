"""Domain service: ToolExecutor — aritmética determinística em Python puro.

Executa tools de function calling invocadas pelo DataAgent.
Zero dependências externas — usa apenas stdlib (decimal, re).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

# Regex para extrair dígitos de strings numéricas/monetárias
_NUMERIC_PATTERN = re.compile(r"[\d.,]+")

# Rótulos que identificam uma linha de totalização (ignoradas em counts/sums)
_TOTAL_LABELS = {"TOTAL", "SUBTOTAL", "GRAND TOTAL", "TOTAIS", "TOTAL GERAL"}


def _parse_numeric(value: str) -> Decimal | None:
    """Converte string numérica ou monetária (BRL) para Decimal.

    Aceita: "R$ 1.234,56", "1.234,56", "1234.56", "1234"
    Retorna None para strings não-numéricas.
    """
    raw = value.strip().lstrip("R$").strip()
    m = _NUMERIC_PATTERN.search(raw)
    if not m:
        return None
    s = m.group()
    # Formato BR: pontos como milhar, vírgula como decimal
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _is_total_row(row: list[str]) -> bool:
    """Detecta linha de totalização — excluída de contagens e somas."""
    if not row:
        return False
    return str(row[0]).strip().upper() in _TOTAL_LABELS


@dataclass(frozen=True)
class ToolResult:
    """Resultado de uma tool invocada pelo DataAgent."""

    tool_name: str
    result: Any  # int | Decimal | list[list[str]] | str | None
    metadata: dict[str, Any]


class ToolExecutor:
    """Executa tools de function calling — Python puro, zero LLM."""

    def execute(
        self,
        tool_name: str,
        args: dict[str, Any],
        tables: list[dict[str, Any]],
    ) -> ToolResult:
        """Despacha para a função correspondente pelo nome da tool."""
        table_index = int(args.get("table_index", 0))
        if table_index < 0 or table_index >= len(tables):
            return ToolResult(
                tool_name=tool_name,
                result=None,
                metadata={
                    "error": f"table_index {table_index} fora do intervalo (total: {len(tables)})"
                },
            )
        table = tables[table_index]

        dispatch = {
            "filter_rows": self._dispatch_filter_rows,
            "count_rows": self._dispatch_count_rows,
            "sum_column": self._dispatch_sum_column,
            "sort_rows": self._dispatch_sort_rows,
            "extract_value": self._dispatch_extract_value,
        }
        handler = dispatch.get(tool_name)
        if handler is None:
            return ToolResult(
                tool_name=tool_name,
                result=None,
                metadata={"error": f"tool '{tool_name}' desconhecida"},
            )
        return handler(args, table)

    # ------------------------------------------------------------------
    # Dispatchers privados (desacoplam argparse da lógica pura)
    # ------------------------------------------------------------------

    def _dispatch_filter_rows(self, args: dict[str, Any], table: dict[str, Any]) -> ToolResult:
        return self.filter_rows(
            table=table,
            column=str(args["column"]),
            operator=str(args["operator"]),
            value=str(args["value"]),
        )

    def _dispatch_count_rows(self, args: dict[str, Any], table: dict[str, Any]) -> ToolResult:
        return self.count_rows(
            table=table,
            column=args.get("column"),
            value=args.get("value"),
        )

    def _dispatch_sum_column(self, args: dict[str, Any], table: dict[str, Any]) -> ToolResult:
        return self.sum_column(table=table, column=str(args["column"]))

    def _dispatch_sort_rows(self, args: dict[str, Any], table: dict[str, Any]) -> ToolResult:
        return self.sort_rows(
            table=table,
            column=str(args["column"]),
            order=str(args.get("order", "desc")),
        )

    def _dispatch_extract_value(self, args: dict[str, Any], table: dict[str, Any]) -> ToolResult:
        return self.extract_value(
            table=table,
            filter_column=str(args["filter_column"]),
            filter_value=str(args["filter_value"]),
            target_column=str(args["target_column"]),
        )

    # ------------------------------------------------------------------
    # Tools públicas (testáveis isoladamente)
    # ------------------------------------------------------------------

    def filter_rows(
        self,
        table: dict[str, Any],
        column: str,
        operator: str,
        value: str,
    ) -> ToolResult:
        """Filtra linhas onde column satisfaz operator comparado a value."""
        headers = list(table.get("headers", []))
        rows = list(table.get("rows", []))

        if column not in headers:
            return ToolResult(
                tool_name="filter_rows",
                result=[],
                metadata={"error": f"coluna '{column}' não encontrada", "headers": headers},
            )

        col_idx = headers.index(column)
        filtered: list[list[str]] = []

        for row in rows:
            if _is_total_row(row):
                continue
            if col_idx >= len(row):
                continue
            if self._matches(str(row[col_idx]), operator, value):
                filtered.append(row)

        return ToolResult(
            tool_name="filter_rows",
            result=filtered,
            metadata={"column": column, "operator": operator, "value": value, "count": len(filtered)},
        )

    def count_rows(
        self,
        table: dict[str, Any],
        column: str | None = None,
        value: str | None = None,
    ) -> ToolResult:
        """Conta linhas da tabela, excluindo linhas de TOTAL.

        Se column e value forem fornecidos, conta apenas as linhas que
        têm esse valor nessa coluna (match exato case-insensitive).
        """
        headers = list(table.get("headers", []))
        rows = list(table.get("rows", []))

        data_rows = [r for r in rows if not _is_total_row(r)]

        if column is not None and value is not None:
            if column not in headers:
                return ToolResult(
                    tool_name="count_rows",
                    result=0,
                    metadata={"error": f"coluna '{column}' não encontrada"},
                )
            col_idx = headers.index(column)
            filter_lower = str(value).strip().lower()
            data_rows = [
                r for r in data_rows
                if col_idx < len(r) and str(r[col_idx]).strip().lower() == filter_lower
            ]

        return ToolResult(
            tool_name="count_rows",
            result=len(data_rows),
            metadata={"column": column, "value": value},
        )

    def sum_column(self, table: dict[str, Any], column: str) -> ToolResult:
        """Soma todos os valores numéricos de uma coluna, ignorando TOTAL e NaN."""
        headers = list(table.get("headers", []))
        rows = list(table.get("rows", []))

        if column not in headers:
            return ToolResult(
                tool_name="sum_column",
                result=Decimal(0),
                metadata={"error": f"coluna '{column}' não encontrada", "headers": headers},
            )

        col_idx = headers.index(column)
        total = Decimal(0)
        summed_rows = 0

        for row in rows:
            if _is_total_row(row):
                continue
            if col_idx >= len(row):
                continue
            v = _parse_numeric(str(row[col_idx]))
            if v is not None:
                total += v
                summed_rows += 1

        return ToolResult(
            tool_name="sum_column",
            result=total,
            metadata={"column": column, "summed_rows": summed_rows},
        )

    def sort_rows(
        self,
        table: dict[str, Any],
        column: str,
        order: str = "desc",
    ) -> ToolResult:
        """Ordena as linhas por column. Valores monetários ordenados numericamente."""
        headers = list(table.get("headers", []))
        rows = list(table.get("rows", []))

        if column not in headers:
            return ToolResult(
                tool_name="sort_rows",
                result=rows,
                metadata={"error": f"coluna '{column}' não encontrada"},
            )

        col_idx = headers.index(column)
        data_rows = [r for r in rows if not _is_total_row(r)]

        def sort_key(row: list[str]) -> tuple[int, Any]:
            if col_idx >= len(row):
                return (1, "")
            cell = str(row[col_idx])
            numeric = _parse_numeric(cell)
            if numeric is not None:
                return (0, numeric)
            return (1, cell.lower())

        reverse = order.lower() == "desc"
        sorted_rows = sorted(data_rows, key=sort_key, reverse=reverse)

        return ToolResult(
            tool_name="sort_rows",
            result=sorted_rows,
            metadata={"column": column, "order": order, "count": len(sorted_rows)},
        )

    def extract_value(
        self,
        table: dict[str, Any],
        filter_column: str,
        filter_value: str,
        target_column: str,
    ) -> ToolResult:
        """Extrai o valor de target_column na linha que match filter_column=filter_value."""
        headers = list(table.get("headers", []))
        rows = list(table.get("rows", []))

        if filter_column not in headers:
            return ToolResult(
                tool_name="extract_value",
                result=None,
                metadata={"error": f"coluna de filtro '{filter_column}' não encontrada"},
            )
        if target_column not in headers:
            return ToolResult(
                tool_name="extract_value",
                result=None,
                metadata={"error": f"coluna alvo '{target_column}' não encontrada"},
            )

        filter_idx = headers.index(filter_column)
        target_idx = headers.index(target_column)
        filter_lower = filter_value.strip().lower()

        for row in rows:
            if _is_total_row(row):
                continue
            if filter_idx >= len(row):
                continue
            if filter_lower in str(row[filter_idx]).strip().lower():
                value = str(row[target_idx]) if target_idx < len(row) else ""
                return ToolResult(
                    tool_name="extract_value",
                    result=value,
                    metadata={
                        "filter_column": filter_column,
                        "filter_value": filter_value,
                        "target_column": target_column,
                    },
                )

        return ToolResult(
            tool_name="extract_value",
            result=None,
            metadata={"error": "linha não encontrada", "filter_value": filter_value},
        )

    # ------------------------------------------------------------------
    # Helper: avaliação de condição
    # ------------------------------------------------------------------

    @staticmethod
    def _matches(cell: str, operator: str, value: str) -> bool:
        """Avalia se cell satisfaz operator comparado a value."""
        cell_lower = cell.strip().lower()
        value_lower = value.strip().lower()

        if operator == "contains":
            return value_lower in cell_lower
        if operator == "startswith":
            return cell_lower.startswith(value_lower)

        # Tenta comparação numérica
        cell_num = _parse_numeric(cell)
        value_num = _parse_numeric(value)

        if cell_num is not None and value_num is not None:
            match operator:
                case "eq":
                    return cell_num == value_num
                case "neq":
                    return cell_num != value_num
                case "gt":
                    return cell_num > value_num
                case "gte":
                    return cell_num >= value_num
                case "lt":
                    return cell_num < value_num
                case "lte":
                    return cell_num <= value_num

        # Fallback para comparação de string
        match operator:
            case "eq":
                return cell_lower == value_lower
            case "neq":
                return cell_lower != value_lower

        return False
