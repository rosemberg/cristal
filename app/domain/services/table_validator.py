"""Domain service: TableValidatorAgent — validates, deduplicates and ranks tables.

Pure domain logic, no I/O. Sits between search_tables() and build_context()
to ensure only well-formed, non-corrupt tables reach the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.entities.document_table import DocumentTable

# Patterns that indicate corrupted cell content
_CORRUPT_PATTERNS = [
    re.compile(r"^;{3,}"),              # ;;;;;;
    re.compile(r"^\|{3,}"),             # ||||||
    re.compile(r"^\[.+\]$"),            # [Nome do Favorecido], [Cargo], etc.
]


@dataclass(frozen=True)
class TableQualityReport:
    """Result of validating a single table."""

    table_id: int
    is_valid: bool
    reason: str
    confidence: float  # 0.0–1.0


class TableValidatorAgent:
    """Validates table quality, deduplicates, and ranks before LLM delivery."""

    def validate(self, table: DocumentTable) -> TableQualityReport:
        """Run all quality checks on a single table."""
        tid = table.id

        # Check 1: Must have more than 1 column
        if len(table.headers) <= 1:
            return TableQualityReport(tid, False, "single_column", 1.0)

        # Check 2: Headers must not contain raw separators
        for header in table.headers:
            if ";" in header or "|||" in header:
                return TableQualityReport(
                    tid, False, "raw_separators_in_headers", 1.0
                )

        # Check 3: All rows empty/whitespace
        has_content = any(
            cell.strip() for row in table.rows for cell in row
        )
        if not has_content:
            return TableQualityReport(tid, False, "all_rows_empty", 1.0)

        # Check 4: High proportion of corrupted cells (> 30%)
        total_cells = sum(len(row) for row in table.rows)
        if total_cells > 0:
            corrupt_cells = sum(
                1
                for row in table.rows
                for cell in row
                if any(p.match(cell.strip()) for p in _CORRUPT_PATTERNS)
            )
            if corrupt_cells / total_cells > 0.3:
                return TableQualityReport(
                    tid, False, "high_corruption_ratio", 0.9
                )

        # Check 5: All rows identical (> 3 rows required to trigger)
        if len(table.rows) > 3:
            unique_rows = {tuple(row) for row in table.rows}
            if len(unique_rows) <= 1:
                return TableQualityReport(
                    tid, False, "all_rows_identical", 0.95
                )

        return TableQualityReport(tid, True, "ok", 1.0)

    def select_best_tables(
        self,
        tables: list[DocumentTable],
        max_tables: int = 5,
    ) -> list[DocumentTable]:
        """Filter, deduplicate, rank and limit tables for LLM context."""
        if not tables:
            return []

        # Step 1: Validate — keep only valid tables
        approved = [t for t in tables if self.validate(t).is_valid]

        # Step 2: Deduplicate by header signature
        approved = self._deduplicate_by_headers(approved)

        # Step 3: Rank by richness (cols * rows), descending
        approved.sort(
            key=lambda t: (t.num_cols or len(t.headers))
            * (t.num_rows or len(t.rows)),
            reverse=True,
        )

        return approved[:max_tables]

    @staticmethod
    def _deduplicate_by_headers(
        tables: list[DocumentTable],
    ) -> list[DocumentTable]:
        """Keep only the richest table per unique header signature."""
        if len(tables) <= 1:
            return list(tables)

        best_per_sig: dict[tuple[str, ...], DocumentTable] = {}
        for t in tables:
            sig = tuple(sorted(h.lower().strip() for h in t.headers))
            existing = best_per_sig.get(sig)
            if existing is None:
                best_per_sig[sig] = t
            else:
                # Prefer the one with more rows
                existing_rows = existing.num_rows or len(existing.rows)
                current_rows = t.num_rows or len(t.rows)
                if current_rows > existing_rows:
                    best_per_sig[sig] = t

        return list(best_per_sig.values())
