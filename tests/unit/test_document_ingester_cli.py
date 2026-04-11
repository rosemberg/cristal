"""Unit tests — DocumentIngesterCLI (Etapa 6).

TDD RED → GREEN para o adapter CLI do pipeline de ingestão.

Cobre:
- DocumentIngesterCLI.run: chama ingest_pending, exibe output formatado
- DocumentIngesterCLI.reprocess: chama reprocess_errors, exibe resultado
- DocumentIngesterCLI.status: chama get_status, exibe contadores incluindo inconsistências
- DocumentIngesterCLI.single: chama ingest_single, exibe sucesso/falha
- DocumentIngesterCLI.check: chama check_all, exibe HealthCheckReport formatado
- DocumentIngesterCLI.inconsistencies: chama get_inconsistencies, exibe tabela
- main(): parsing de argumentos, roteamento para métodos corretos
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from io import StringIO
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from app.adapters.inbound.cli.document_ingester import DocumentIngesterCLI, main
from app.domain.value_objects.data_inconsistency import DataInconsistency, HealthCheckReport
from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus

# ── Constantes ────────────────────────────────────────────────────────────────

NOW = datetime(2026, 4, 11, 12, 0, 0, tzinfo=timezone.utc)
DOC_URL = "https://www.tre-pi.jus.br/doc/resolucao-456.pdf"


# ── Helpers ───────────────────────────────────────────────────────────────────


def make_ingestion_stats(
    total: int = 122,
    processed: int = 118,
    errors: int = 4,
    skipped: int = 0,
    duration_seconds: float = 272.0,
    inconsistencies_found: int = 4,
) -> IngestionStats:
    return IngestionStats(
        total=total,
        processed=processed,
        errors=errors,
        skipped=skipped,
        duration_seconds=duration_seconds,
        inconsistencies_found=inconsistencies_found,
    )


def make_ingestion_status(
    pending: int = 10,
    processing: int = 2,
    done: int = 108,
    error: int = 4,
    total_chunks: int = 1847,
    total_tables: int = 89,
    open_inconsistencies: int = 12,
) -> IngestionStatus:
    return IngestionStatus(
        pending=pending,
        processing=processing,
        done=done,
        error=error,
        total_chunks=total_chunks,
        total_tables=total_tables,
        open_inconsistencies=open_inconsistencies,
    )


def make_health_report(
    total_checked: int = 2259,
    healthy: int = 2199,
    issues_found: int = 60,
    new_inconsistencies: int = 60,
    updated_inconsistencies: int = 3,
    auto_resolved: int = 7,
    duration_seconds: float = 765.0,
    by_type: dict[str, int] | None = None,
) -> HealthCheckReport:
    return HealthCheckReport(
        total_checked=total_checked,
        healthy=healthy,
        issues_found=issues_found,
        new_inconsistencies=new_inconsistencies,
        updated_inconsistencies=updated_inconsistencies,
        auto_resolved=auto_resolved,
        duration_seconds=duration_seconds,
        by_type=by_type or {"broken_link": 42, "page_not_accessible": 11, "document_not_found": 7},
    )


def make_inconsistency(
    id: int = 23,
    resource_type: str = "document",
    severity: str = "critical",
    inconsistency_type: str = "document_not_found",
    resource_url: str = DOC_URL,
    resource_title: str | None = "resolucao-456.pdf",
    detail: str = "HTTP 404",
    status: str = "open",
) -> DataInconsistency:
    return DataInconsistency(
        id=id,
        resource_type=resource_type,
        severity=severity,
        inconsistency_type=inconsistency_type,
        resource_url=resource_url,
        resource_title=resource_title,
        parent_page_url="https://www.tre-pi.jus.br/licitacoes",
        detail=detail,
        http_status=404,
        error_message=None,
        detected_at=NOW,
        detected_by="health_check",
        status=status,
        resolved_at=None,
        resolved_by=None,
        resolution_note=None,
        retry_count=0,
        last_checked_at=NOW,
    )


def make_cli(
    ingestion_service: object | None = None,
    health_check_service: object | None = None,
) -> DocumentIngesterCLI:
    ing = ingestion_service or AsyncMock()
    hc = health_check_service or AsyncMock()
    return DocumentIngesterCLI(
        ingestion_service=ing,
        health_check_service=hc,
    )


# ── Testes: instanciação ──────────────────────────────────────────────────────


class TestDocumentIngesterCLIInit:
    def test_init_stores_services(self) -> None:
        ing = AsyncMock()
        hc = AsyncMock()
        cli = DocumentIngesterCLI(ingestion_service=ing, health_check_service=hc)
        assert cli._ingestion_service is ing
        assert cli._health_check_service is hc


# ── Testes: run ───────────────────────────────────────────────────────────────


class TestDocumentIngesterCLIRun:
    @pytest.mark.asyncio
    async def test_run_calls_ingest_pending_with_default_concurrency(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ing = AsyncMock()
        ing.ingest_pending = AsyncMock(return_value=make_ingestion_stats())
        cli = make_cli(ingestion_service=ing)

        await cli.run()

        ing.ingest_pending.assert_awaited_once_with(concurrency=3)

    @pytest.mark.asyncio
    async def test_run_calls_ingest_pending_with_custom_concurrency(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ing = AsyncMock()
        ing.ingest_pending = AsyncMock(return_value=make_ingestion_stats())
        cli = make_cli(ingestion_service=ing)

        await cli.run(concurrency=5)

        ing.ingest_pending.assert_awaited_once_with(concurrency=5)

    @pytest.mark.asyncio
    async def test_run_prints_header(self, capsys: pytest.CaptureFixture[str]) -> None:
        ing = AsyncMock()
        ing.ingest_pending = AsyncMock(return_value=make_ingestion_stats())
        cli = make_cli(ingestion_service=ing)

        await cli.run()

        out = capsys.readouterr().out
        assert "Ingestão de Documentos" in out

    @pytest.mark.asyncio
    async def test_run_prints_result_summary(self, capsys: pytest.CaptureFixture[str]) -> None:
        ing = AsyncMock()
        stats = make_ingestion_stats(
            total=122, processed=118, errors=4,
            duration_seconds=272.0, inconsistencies_found=4,
        )
        ing.ingest_pending = AsyncMock(return_value=stats)
        cli = make_cli(ingestion_service=ing)

        await cli.run()

        out = capsys.readouterr().out
        assert "118" in out  # processados
        assert "4" in out    # erros
        assert "122" in out  # total

    @pytest.mark.asyncio
    async def test_run_prints_inconsistencies_count(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ing = AsyncMock()
        stats = make_ingestion_stats(inconsistencies_found=4)
        ing.ingest_pending = AsyncMock(return_value=stats)
        cli = make_cli(ingestion_service=ing)

        await cli.run()

        out = capsys.readouterr().out
        assert "nconsistên" in out  # "Inconsistências" or "inconsistências"

    @pytest.mark.asyncio
    async def test_run_prints_duration(self, capsys: pytest.CaptureFixture[str]) -> None:
        ing = AsyncMock()
        stats = make_ingestion_stats(duration_seconds=272.0)
        ing.ingest_pending = AsyncMock(return_value=stats)
        cli = make_cli(ingestion_service=ing)

        await cli.run()

        out = capsys.readouterr().out
        # Deve mostrar duração em algum formato (ex: "4m 32s" ou "272s")
        assert "m" in out or "s" in out


# ── Testes: reprocess ─────────────────────────────────────────────────────────


class TestDocumentIngesterCLIReprocess:
    @pytest.mark.asyncio
    async def test_reprocess_calls_reprocess_errors(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ing = AsyncMock()
        ing.reprocess_errors = AsyncMock(return_value=make_ingestion_stats(total=4, processed=3, errors=1))
        cli = make_cli(ingestion_service=ing)

        await cli.reprocess()

        ing.reprocess_errors.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reprocess_prints_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        ing = AsyncMock()
        stats = make_ingestion_stats(total=4, processed=3, errors=1)
        ing.reprocess_errors = AsyncMock(return_value=stats)
        cli = make_cli(ingestion_service=ing)

        await cli.reprocess()

        out = capsys.readouterr().out
        assert "3" in out
        assert "eprocess" in out.lower() or "Reprocessamento" in out


# ── Testes: status ────────────────────────────────────────────────────────────


class TestDocumentIngesterCLIStatus:
    @pytest.mark.asyncio
    async def test_status_calls_get_status(self, capsys: pytest.CaptureFixture[str]) -> None:
        ing = AsyncMock()
        ing.get_status = AsyncMock(return_value=make_ingestion_status())
        cli = make_cli(ingestion_service=ing)

        await cli.status()

        ing.get_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_status_prints_pending_count(self, capsys: pytest.CaptureFixture[str]) -> None:
        ing = AsyncMock()
        ing.get_status = AsyncMock(return_value=make_ingestion_status(pending=10))
        cli = make_cli(ingestion_service=ing)

        await cli.status()

        out = capsys.readouterr().out
        assert "10" in out
        assert "endente" in out or "Pendente" in out

    @pytest.mark.asyncio
    async def test_status_prints_done_and_error(self, capsys: pytest.CaptureFixture[str]) -> None:
        ing = AsyncMock()
        ing.get_status = AsyncMock(return_value=make_ingestion_status(done=108, error=4))
        cli = make_cli(ingestion_service=ing)

        await cli.status()

        out = capsys.readouterr().out
        assert "108" in out
        assert "4" in out

    @pytest.mark.asyncio
    async def test_status_prints_open_inconsistencies(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ing = AsyncMock()
        ing.get_status = AsyncMock(
            return_value=make_ingestion_status(open_inconsistencies=12)
        )
        cli = make_cli(ingestion_service=ing)

        await cli.status()

        out = capsys.readouterr().out
        assert "12" in out
        assert "nconsistên" in out

    @pytest.mark.asyncio
    async def test_status_prints_chunks_and_tables(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ing = AsyncMock()
        ing.get_status = AsyncMock(
            return_value=make_ingestion_status(total_chunks=1847, total_tables=89)
        )
        cli = make_cli(ingestion_service=ing)

        await cli.status()

        out = capsys.readouterr().out
        assert "1847" in out
        assert "89" in out


# ── Testes: single ────────────────────────────────────────────────────────────


class TestDocumentIngesterCLISingle:
    @pytest.mark.asyncio
    async def test_single_calls_ingest_single_with_url(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ing = AsyncMock()
        ing.ingest_single = AsyncMock(return_value=True)
        cli = make_cli(ingestion_service=ing)

        await cli.single(DOC_URL)

        ing.ingest_single.assert_awaited_once_with(DOC_URL)

    @pytest.mark.asyncio
    async def test_single_prints_success_when_true(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ing = AsyncMock()
        ing.ingest_single = AsyncMock(return_value=True)
        cli = make_cli(ingestion_service=ing)

        await cli.single(DOC_URL)

        out = capsys.readouterr().out
        assert "OK" in out or "sucesso" in out.lower() or "processado" in out.lower()

    @pytest.mark.asyncio
    async def test_single_prints_failure_when_false(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        ing = AsyncMock()
        ing.ingest_single = AsyncMock(return_value=False)
        cli = make_cli(ingestion_service=ing)

        await cli.single(DOC_URL)

        out = capsys.readouterr().out
        assert "erro" in out.lower() or "falh" in out.lower() or "ERRO" in out


# ── Testes: check ─────────────────────────────────────────────────────────────


class TestDocumentIngesterCLICheck:
    @pytest.mark.asyncio
    async def test_check_calls_check_all(self, capsys: pytest.CaptureFixture[str]) -> None:
        hc = AsyncMock()
        hc.check_all = AsyncMock(return_value=make_health_report())
        cli = make_cli(health_check_service=hc)

        await cli.check()

        hc.check_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_prints_header(self, capsys: pytest.CaptureFixture[str]) -> None:
        hc = AsyncMock()
        hc.check_all = AsyncMock(return_value=make_health_report())
        cli = make_cli(health_check_service=hc)

        await cli.check()

        out = capsys.readouterr().out
        assert "Health Check" in out

    @pytest.mark.asyncio
    async def test_check_prints_total_checked(self, capsys: pytest.CaptureFixture[str]) -> None:
        hc = AsyncMock()
        hc.check_all = AsyncMock(return_value=make_health_report(total_checked=2259))
        cli = make_cli(health_check_service=hc)

        await cli.check()

        out = capsys.readouterr().out
        assert "2259" in out

    @pytest.mark.asyncio
    async def test_check_prints_new_inconsistencies(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hc = AsyncMock()
        hc.check_all = AsyncMock(return_value=make_health_report(new_inconsistencies=60))
        cli = make_cli(health_check_service=hc)

        await cli.check()

        out = capsys.readouterr().out
        assert "60" in out

    @pytest.mark.asyncio
    async def test_check_prints_auto_resolved(self, capsys: pytest.CaptureFixture[str]) -> None:
        hc = AsyncMock()
        hc.check_all = AsyncMock(return_value=make_health_report(auto_resolved=7))
        cli = make_cli(health_check_service=hc)

        await cli.check()

        out = capsys.readouterr().out
        assert "7" in out
        assert "auto" in out.lower() or "resolvid" in out.lower()

    @pytest.mark.asyncio
    async def test_check_prints_duration(self, capsys: pytest.CaptureFixture[str]) -> None:
        hc = AsyncMock()
        hc.check_all = AsyncMock(return_value=make_health_report(duration_seconds=765.0))
        cli = make_cli(health_check_service=hc)

        await cli.check()

        out = capsys.readouterr().out
        assert "m" in out or "s" in out


# ── Testes: inconsistencies ───────────────────────────────────────────────────


class TestDocumentIngesterCLIInconsistencies:
    @pytest.mark.asyncio
    async def test_inconsistencies_calls_get_inconsistencies_defaults(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hc = AsyncMock()
        hc.get_inconsistencies = AsyncMock(return_value=[])
        cli = make_cli(health_check_service=hc)

        await cli.inconsistencies()

        hc.get_inconsistencies.assert_awaited_once_with(
            status="open",
            severity=None,
            resource_type=None,
            inconsistency_type=None,
        )

    @pytest.mark.asyncio
    async def test_inconsistencies_passes_severity_filter(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hc = AsyncMock()
        hc.get_inconsistencies = AsyncMock(return_value=[])
        cli = make_cli(health_check_service=hc)

        await cli.inconsistencies(severity="critical")

        hc.get_inconsistencies.assert_awaited_once_with(
            status="open",
            severity="critical",
            resource_type=None,
            inconsistency_type=None,
        )

    @pytest.mark.asyncio
    async def test_inconsistencies_passes_type_filter(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hc = AsyncMock()
        hc.get_inconsistencies = AsyncMock(return_value=[])
        cli = make_cli(health_check_service=hc)

        await cli.inconsistencies(inconsistency_type="broken_link")

        hc.get_inconsistencies.assert_awaited_once_with(
            status="open",
            severity=None,
            resource_type=None,
            inconsistency_type="broken_link",
        )

    @pytest.mark.asyncio
    async def test_inconsistencies_prints_header(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hc = AsyncMock()
        hc.get_inconsistencies = AsyncMock(return_value=[])
        cli = make_cli(health_check_service=hc)

        await cli.inconsistencies()

        out = capsys.readouterr().out
        assert "nconsistên" in out or "Inconsistencias" in out

    @pytest.mark.asyncio
    async def test_inconsistencies_prints_table_with_data(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hc = AsyncMock()
        inc = make_inconsistency(id=23, inconsistency_type="document_not_found", severity="critical")
        hc.get_inconsistencies = AsyncMock(return_value=[inc])
        cli = make_cli(health_check_service=hc)

        await cli.inconsistencies()

        out = capsys.readouterr().out
        assert "23" in out
        assert "document_not_found" in out
        assert "critical" in out

    @pytest.mark.asyncio
    async def test_inconsistencies_prints_empty_message_when_none(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hc = AsyncMock()
        hc.get_inconsistencies = AsyncMock(return_value=[])
        cli = make_cli(health_check_service=hc)

        await cli.inconsistencies()

        out = capsys.readouterr().out
        assert "0" in out or "nenhuma" in out.lower() or "Nenhuma" in out

    @pytest.mark.asyncio
    async def test_inconsistencies_prints_total_count(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        hc = AsyncMock()
        items = [make_inconsistency(id=i) for i in range(5)]
        hc.get_inconsistencies = AsyncMock(return_value=items)
        cli = make_cli(health_check_service=hc)

        await cli.inconsistencies()

        out = capsys.readouterr().out
        assert "5" in out


# ── Testes: main() — parsing de argumentos ───────────────────────────────────


class TestMainArgparse:
    def _run_main(self, args: list[str]) -> None:
        """Executa main() com sys.argv mockado."""
        with patch("sys.argv", ["document_ingester"] + args):
            main()

    def test_main_run_calls_cli_run(self) -> None:
        with (
            patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
        ):
            mock_cli = MagicMock()
            mock_build.return_value = mock_cli

            self._run_main(["--run"])

            mock_async.assert_called_once()

    def test_main_no_args_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        with (
            patch("sys.argv", ["document_ingester"]),
            pytest.raises(SystemExit),
        ):
            main()

    def test_main_run_with_concurrency(self) -> None:
        with (
            patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
        ):
            mock_cli = MagicMock()
            mock_build.return_value = mock_cli

            self._run_main(["--run", "--concurrency", "5"])

            mock_async.assert_called_once()

    def test_main_status_calls_cli_status(self) -> None:
        with (
            patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
        ):
            mock_cli = MagicMock()
            mock_build.return_value = mock_cli

            self._run_main(["--status"])

            mock_async.assert_called_once()

    def test_main_reprocess_calls_cli_reprocess(self) -> None:
        with (
            patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
        ):
            mock_cli = MagicMock()
            mock_build.return_value = mock_cli

            self._run_main(["--reprocess"])

            mock_async.assert_called_once()

    def test_main_url_calls_cli_single(self) -> None:
        with (
            patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
        ):
            mock_cli = MagicMock()
            mock_build.return_value = mock_cli

            self._run_main(["--url", DOC_URL])

            mock_async.assert_called_once()

    def test_main_check_calls_cli_check(self) -> None:
        with (
            patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
        ):
            mock_cli = MagicMock()
            mock_build.return_value = mock_cli

            self._run_main(["--check"])

            mock_async.assert_called_once()

    def test_main_inconsistencies_calls_cli_inconsistencies(self) -> None:
        with (
            patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
        ):
            mock_cli = MagicMock()
            mock_build.return_value = mock_cli

            self._run_main(["--inconsistencies"])

            mock_async.assert_called_once()

    def test_main_inconsistencies_with_severity(self) -> None:
        with (
            patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
        ):
            mock_cli = MagicMock()
            mock_build.return_value = mock_cli

            self._run_main(["--inconsistencies", "--severity", "critical"])

            mock_async.assert_called_once()

    def test_main_inconsistencies_with_type(self) -> None:
        with (
            patch("app.adapters.inbound.cli.document_ingester._build_cli") as mock_build,
            patch("app.adapters.inbound.cli.document_ingester.asyncio.run") as mock_async,
        ):
            mock_cli = MagicMock()
            mock_build.return_value = mock_cli

            self._run_main(["--inconsistencies", "--type", "broken_link"])

            mock_async.assert_called_once()


# ── Testes: formatação de duração ─────────────────────────────────────────────


class TestFormatDuration:
    def test_format_under_60_seconds(self) -> None:
        from app.adapters.inbound.cli.document_ingester import _format_duration

        assert "45s" in _format_duration(45.0)

    def test_format_minutes_and_seconds(self) -> None:
        from app.adapters.inbound.cli.document_ingester import _format_duration

        result = _format_duration(272.0)
        assert "4m" in result
        assert "32s" in result

    def test_format_exact_minute(self) -> None:
        from app.adapters.inbound.cli.document_ingester import _format_duration

        result = _format_duration(60.0)
        assert "1m" in result

    def test_format_hours(self) -> None:
        from app.adapters.inbound.cli.document_ingester import _format_duration

        result = _format_duration(3661.0)
        assert "1h" in result
