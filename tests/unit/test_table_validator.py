"""Unit tests — TableValidatorAgent (domain service).

Validates table quality, detects corruption, and deduplicates
tables before they reach the LLM context.
"""

from __future__ import annotations

import pytest

from app.domain.entities.document_table import DocumentTable
from app.domain.services.table_validator import TableQualityReport, TableValidatorAgent


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_table(
    *,
    id: int = 1,
    headers: list[str] | None = None,
    rows: list[list[str]] | None = None,
    document_url: str = "https://example.com/data.csv",
    caption: str | None = None,
    num_cols: int | None = None,
    num_rows: int | None = None,
) -> DocumentTable:
    headers = ["Col A", "Col B", "Col C"] if headers is None else headers
    rows = [["a1", "b1", "c1"], ["a2", "b2", "c2"]] if rows is None else rows
    return DocumentTable(
        id=id,
        document_url=document_url,
        table_index=0,
        headers=headers,
        rows=rows,
        caption=caption,
        num_rows=num_rows if num_rows is not None else len(rows),
        num_cols=num_cols if num_cols is not None else len(headers),
    )


# ─── Structural validation ──────────────────────────────────────────────────


class TestStructuralValidation:
    """Tables with structural defects must be rejected."""

    def test_single_column_table_rejected(self) -> None:
        table = _make_table(
            headers=["DIÁRIAS PAGAS PELO TRE-PI NO MÊS DE JUNHO DE 2022;;;;;;"],
            rows=[[";;;;;;"], ["Favorecido;CARGO;Data Inicial;..."]],
            num_cols=1,
        )
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert not report.is_valid
        assert report.reason == "single_column"

    def test_empty_headers_rejected(self) -> None:
        table = _make_table(headers=[], rows=[], num_cols=0)
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert not report.is_valid

    def test_multi_column_table_accepted(self) -> None:
        table = _make_table()
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert report.is_valid
        assert report.reason == "ok"


# ─── Separator in headers ───────────────────────────────────────────────────


class TestSeparatorInHeaders:
    """Headers containing raw separators indicate wrong CSV parse."""

    def test_semicolons_in_headers_rejected(self) -> None:
        table = _make_table(
            headers=["Nome;Cargo;Valor", "Outro"],
            rows=[["data", "data2"]],
        )
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert not report.is_valid
        assert report.reason == "raw_separators_in_headers"

    def test_pipes_in_headers_rejected(self) -> None:
        table = _make_table(
            headers=["Nome|||Cargo|||Valor", "Outro"],
            rows=[["data", "data2"]],
        )
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert not report.is_valid
        assert report.reason == "raw_separators_in_headers"

    def test_clean_headers_accepted(self) -> None:
        table = _make_table(headers=["Favorecido", "Cargo", "Valor"])
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert report.is_valid


# ─── Corruption detection ───────────────────────────────────────────────────


class TestCorruptionDetection:
    """Tables with high proportion of corrupted cells must be rejected."""

    def test_semicolon_rows_rejected(self) -> None:
        table = _make_table(
            headers=["A", "B"],
            rows=[
                [";;;;;;", ";;;;;;"],
                [";;;;;;", ";;;;;;"],
                [";;;;;;", ";;;;;;"],
                [";;;;;;", ";;;;;;"],
            ],
        )
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert not report.is_valid
        assert report.reason == "high_corruption_ratio"

    def test_placeholder_rows_rejected(self) -> None:
        table = _make_table(
            headers=["Favorecido", "Cargo", "Valor"],
            rows=[
                ["[Nome do Favorecido]", "[Cargo]", "[Valor]"],
                ["[Nome do Favorecido]", "[Cargo]", "[Valor]"],
                ["[Nome do Favorecido]", "[Cargo]", "[Valor]"],
            ],
        )
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert not report.is_valid
        assert report.reason == "high_corruption_ratio"

    def test_few_corrupt_cells_still_accepted(self) -> None:
        """A table with < 30% corrupt cells should still pass."""
        table = _make_table(
            headers=["A", "B", "C"],
            rows=[
                ["real data", "more data", "ok"],
                ["real data", ";;;;;;", "ok"],
                ["real data", "more data", "ok"],
                ["real data", "more data", "ok"],
            ],
        )
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert report.is_valid


class TestAllRowsIdentical:
    """Tables where all rows are the same are likely junk."""

    def test_identical_rows_rejected(self) -> None:
        table = _make_table(
            headers=["A", "B"],
            rows=[["x", "y"]] * 5,
        )
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert not report.is_valid
        assert report.reason == "all_rows_identical"

    def test_two_identical_rows_accepted(self) -> None:
        """Small tables with few rows shouldn't trigger this rule."""
        table = _make_table(
            headers=["A", "B"],
            rows=[["x", "y"]] * 2,
        )
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert report.is_valid


class TestAllRowsEmpty:
    """Tables where all cells are empty/whitespace are useless."""

    def test_empty_rows_rejected(self) -> None:
        table = _make_table(
            headers=["A", "B"],
            rows=[["", "  "], ["  ", ""]],
        )
        validator = TableValidatorAgent()
        report = validator.validate(table)

        assert not report.is_valid
        assert report.reason == "all_rows_empty"


# ─── Deduplication ───────────────────────────────────────────────────────────


class TestDeduplication:
    """When multiple tables share the same header signature, keep the richest."""

    def test_duplicate_headers_keeps_richest(self) -> None:
        poor = _make_table(
            id=1,
            headers=["Favorecido", "Cargo", "Valor"],
            rows=[["a", "b", "c"]],
            document_url="https://example.com/diarias.csv",
        )
        rich = _make_table(
            id=2,
            headers=["Favorecido", "Cargo", "Valor"],
            rows=[["a", "b", "c"], ["d", "e", "f"], ["g", "h", "i"]],
            document_url="https://example.com/diarias.pdf",
        )
        validator = TableValidatorAgent()
        result = validator.select_best_tables([poor, rich])

        assert len(result) == 1
        assert result[0].id == 2

    def test_different_headers_kept_separate(self) -> None:
        t1 = _make_table(
            id=1,
            headers=["Favorecido", "Cargo", "Valor"],
            rows=[["a", "b", "c"]],
        )
        t2 = _make_table(
            id=2,
            headers=["Contrato", "Vigencia", "Valor"],
            rows=[["x", "y", "z"]],
        )
        validator = TableValidatorAgent()
        result = validator.select_best_tables([t1, t2])

        assert len(result) == 2


# ─── select_best_tables integration ─────────────────────────────────────────


class TestSelectBestTables:
    """Full pipeline: validate → deduplicate → rank → limit."""

    def test_corrupt_tables_filtered_out(self) -> None:
        good = _make_table(id=1, headers=["A", "B"], rows=[["1", "2"]])
        corrupt = _make_table(
            id=2,
            headers=["DIÁRIAS;;;;;;"],
            rows=[[";;;;;;"]],
            num_cols=1,
        )
        validator = TableValidatorAgent()
        result = validator.select_best_tables([good, corrupt])

        assert len(result) == 1
        assert result[0].id == 1

    def test_max_tables_respected(self) -> None:
        tables = [
            _make_table(id=i, headers=["A", "B", f"C{i}"], rows=[["1", "2", "3"]])
            for i in range(10)
        ]
        validator = TableValidatorAgent()
        result = validator.select_best_tables(tables, max_tables=3)

        assert len(result) == 3

    def test_ranking_prefers_richer_tables(self) -> None:
        small = _make_table(
            id=1,
            headers=["A", "B"],
            rows=[["1", "2"]],
            num_cols=2,
            num_rows=1,
            caption="small",
        )
        big = _make_table(
            id=2,
            headers=["A", "B", "C", "D"],
            rows=[[str(i), "2", "3", "4"] for i in range(10)],
            num_cols=4,
            num_rows=10,
            caption="big",
        )
        validator = TableValidatorAgent()
        result = validator.select_best_tables([small, big], max_tables=2)

        # Big table should come first (higher richness score: 4*10=40 vs 2*1=2)
        assert len(result) == 2
        assert result[0].id == 2

    def test_empty_input_returns_empty(self) -> None:
        validator = TableValidatorAgent()
        assert validator.select_best_tables([]) == []


# ─── QualityReport dataclass ────────────────────────────────────────────────


class TestQualityReport:
    def test_report_fields(self) -> None:
        report = TableQualityReport(
            table_id=42, is_valid=True, reason="ok", confidence=1.0
        )
        assert report.table_id == 42
        assert report.is_valid
        assert report.reason == "ok"
        assert report.confidence == 1.0
