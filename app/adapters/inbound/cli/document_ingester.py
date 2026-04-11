"""CLI adapter: Pipeline de ingestão de documentos — TRE-PI.

Executa ingestão de documentos e health check via linha de comando.

Uso:
    python -m app.adapters.inbound.cli.document_ingester --run
    python -m app.adapters.inbound.cli.document_ingester --run --concurrency 5
    python -m app.adapters.inbound.cli.document_ingester --status
    python -m app.adapters.inbound.cli.document_ingester --reprocess
    python -m app.adapters.inbound.cli.document_ingester --url https://...pdf
    python -m app.adapters.inbound.cli.document_ingester --check
    python -m app.adapters.inbound.cli.document_ingester --inconsistencies
    python -m app.adapters.inbound.cli.document_ingester --inconsistencies --severity critical
    python -m app.adapters.inbound.cli.document_ingester --inconsistencies --type broken_link
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

from app.domain.ports.inbound.data_health_check_use_case import DataHealthCheckUseCase
from app.domain.ports.inbound.document_ingestion_use_case import DocumentIngestionUseCase
from app.domain.value_objects.data_inconsistency import DataInconsistency, HealthCheckReport
from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus

logger = logging.getLogger(__name__)

# ── Utilitários de formatação ─────────────────────────────────────────────────


def _format_duration(seconds: float) -> str:
    """Formata duração em segundos para string legível."""
    total = int(seconds)
    if total < 60:
        return f"{total}s"
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def _severity_icon(severity: str) -> str:
    icons = {"critical": "[!]", "warning": "[~]", "info": "[i]"}
    return icons.get(severity, "[ ]")


def _truncate(text: str, max_len: int = 40) -> str:
    return text if len(text) <= max_len else text[: max_len - 3] + "..."


# ── DocumentIngesterCLI ───────────────────────────────────────────────────────


class DocumentIngesterCLI:
    """Adapter CLI para o pipeline de ingestão e health check de documentos."""

    def __init__(
        self,
        ingestion_service: DocumentIngestionUseCase,
        health_check_service: DataHealthCheckUseCase,
    ) -> None:
        self._ingestion_service = ingestion_service
        self._health_check_service = health_check_service

    # ── run ───────────────────────────────────────────────────────────────────

    async def run(self, concurrency: int = 3) -> None:
        """Executa ingestão de todos os documentos pendentes."""
        print("\n=== Ingestão de Documentos ===")
        print(f"Processando com concorrência: {concurrency}")
        print()

        stats: IngestionStats = await self._ingestion_service.ingest_pending(
            concurrency=concurrency
        )

        print("=== Resultado ===")
        print(f"Processados:              {stats.processed}/{stats.total}")
        print(f"Erros:                    {stats.errors}")
        print(f"Inconsistências:          {stats.inconsistencies_found}")
        print(f"Duração:                  {_format_duration(stats.duration_seconds)}")

    # ── reprocess ─────────────────────────────────────────────────────────────

    async def reprocess(self) -> None:
        """Reprocessa documentos que estão com status 'error'."""
        print("\n=== Reprocessamento de Erros ===")

        stats: IngestionStats = await self._ingestion_service.reprocess_errors()

        print("=== Resultado ===")
        print(f"Processados:              {stats.processed}/{stats.total}")
        print(f"Erros restantes:          {stats.errors}")
        print(f"Inconsistências:          {stats.inconsistencies_found}")
        print(f"Duração:                  {_format_duration(stats.duration_seconds)}")

    # ── status ────────────────────────────────────────────────────────────────

    async def status(self) -> None:
        """Exibe snapshot dos contadores do pipeline."""
        status: IngestionStatus = await self._ingestion_service.get_status()

        print("\n=== Status do Pipeline ===")
        print(f"Pendentes:                {status.pending}")
        print(f"Em processamento:         {status.processing}")
        print(f"Concluídos:               {status.done}")
        print(f"Erros:                    {status.error}")
        print(f"Total de chunks:          {status.total_chunks}")
        print(f"Total de tabelas:         {status.total_tables}")
        print(f"Inconsistências abertas:  {status.open_inconsistencies}")

    # ── single ────────────────────────────────────────────────────────────────

    async def single(self, url: str) -> None:
        """Processa um único documento por URL."""
        print(f"\nProcessando: {url}")

        success: bool = await self._ingestion_service.ingest_single(url)

        if success:
            print("OK — documento processado com sucesso.")
        else:
            print("ERRO — falha ao processar o documento.")

    # ── check ─────────────────────────────────────────────────────────────────

    async def check(self) -> None:
        """Executa health check completo e exibe o relatório."""
        print("\n=== Health Check de Dados ===")

        report: HealthCheckReport = await self._health_check_service.check_all()

        print()
        print("=== Resumo ===")
        print(f"Total verificado:         {report.total_checked}")
        print(f"Saudáveis:                {report.healthy}")
        print(f"Problemas encontrados:    {report.issues_found}")
        print(f"Novas inconsistências:    {report.new_inconsistencies}")
        print(f"Atualizadas:              {report.updated_inconsistencies}")
        print(f"Auto-resolvidas:          {report.auto_resolved}")
        print(f"Duração:                  {_format_duration(report.duration_seconds)}")

        if report.by_type:
            print("\nInconsistências por tipo:")
            for itype, count in sorted(report.by_type.items(), key=lambda x: -x[1]):
                print(f"  {itype:<30} {count}")

    # ── inconsistencies ───────────────────────────────────────────────────────

    async def inconsistencies(
        self,
        severity: str | None = None,
        resource_type: str | None = None,
        inconsistency_type: str | None = None,
        status: str = "open",
    ) -> None:
        """Lista inconsistências com filtros opcionais."""
        items: list[DataInconsistency] = await self._health_check_service.get_inconsistencies(
            status=status,
            severity=severity,
            resource_type=resource_type,
            inconsistency_type=inconsistency_type,
        )

        print("\n=== Inconsistências Abertas ===")

        if not items:
            print(f"\nNenhuma inconsistência encontrada (total: 0).")
            return

        # Cabeçalho da tabela
        print(
            f"\n {'ID':>4} | {'Tipo':<25} | {'Severidade':<10} | {'Recurso':<40} | {'Detectado'}"
        )
        print(f" {'-'*4}-+-{'-'*25}-+-{'-'*10}-+-{'-'*40}-+-{'-'*10}")

        for inc in items:
            detected = inc.detected_at.strftime("%Y-%m-%d") if inc.detected_at else "—"
            resource = _truncate(inc.resource_title or inc.resource_url, 40)
            print(
                f" {inc.id or '?':>4} | {inc.inconsistency_type:<25} | "
                f"{inc.severity:<10} | {resource:<40} | {detected}"
            )

        # Contagens por severidade
        critical = sum(1 for i in items if i.severity == "critical")
        warning = sum(1 for i in items if i.severity == "warning")
        info = sum(1 for i in items if i.severity == "info")

        print(
            f"\nTotal: {len(items)} abertas "
            f"({critical} critical, {warning} warning, {info} info)"
        )


# ── Factory ───────────────────────────────────────────────────────────────────


def _build_cli() -> DocumentIngesterCLI:
    """Inicializa dependências e retorna instância de DocumentIngesterCLI."""
    from app.adapters.outbound.postgres.connection import DatabasePool, get_pool
    from app.adapters.outbound.postgres.document_ingestion_repo import (
        PostgresDocumentIngestionRepository,
    )
    from app.adapters.outbound.postgres.inconsistency_repo import PostgresInconsistencyRepository
    from app.adapters.outbound.postgres.page_repo import PostgresPageRepository
    from app.config.settings import get_settings
    from app.domain.services.data_health_check_service import DataHealthCheckService
    from app.domain.services.document_ingestion_service import DocumentIngestionService
    from app.infrastructure.http.document_download_adapter import DocumentDownloadAdapter
    from app.infrastructure.process.document_process_adapter import DocumentProcessAdapter

    settings = get_settings()
    # Nota: DatabasePool é gerenciado pelo caller (main), retornamos a factory
    # O caller deve usar o context manager. Aqui retornamos a factory function.
    raise NotImplementedError(
        "Use _build_cli_async() dentro de um contexto assíncrono com DatabasePool."
    )


async def _build_cli_async() -> tuple[DocumentIngesterCLI, object]:
    """Constrói CLI e retorna (cli, db_pool) para gerenciamento do ciclo de vida."""
    from app.adapters.outbound.postgres.connection import DatabasePool, get_pool
    from app.config.settings import get_settings

    settings = get_settings()

    # Importações lazy para não penalizar --help
    from app.adapters.outbound.postgres.document_ingestion_repo import (
        PostgresDocumentIngestionRepository,
    )
    from app.adapters.outbound.postgres.inconsistency_repo import PostgresInconsistencyRepository
    from app.adapters.outbound.postgres.page_repo import PostgresPageRepository
    from app.domain.services.data_health_check_service import DataHealthCheckService
    from app.domain.services.document_ingestion_service import DocumentIngestionService

    db = DatabasePool(settings)
    await db.__aenter__()
    pool = get_pool(db)

    doc_repo = PostgresDocumentIngestionRepository(pool)
    page_repo = PostgresPageRepository(pool)
    inconsistency_repo = PostgresInconsistencyRepository(pool)

    # Download gateway
    try:
        from app.adapters.outbound.http.document_download_gateway import (
            HttpDocumentDownloadGateway,
        )
        download_gw = HttpDocumentDownloadGateway()
    except ImportError:
        from app.adapters.outbound.http.document_downloader import HttpDocumentDownloader
        download_gw = HttpDocumentDownloader()

    # Process gateway
    try:
        from app.adapters.outbound.process.document_process_gateway import (
            DefaultDocumentProcessGateway,
        )
        process_gw = DefaultDocumentProcessGateway()
    except ImportError:
        from app.adapters.outbound.process.document_process_adapter import (
            DocumentProcessAdapter,
        )
        process_gw = DocumentProcessAdapter()

    ingestion_service = DocumentIngestionService(
        document_repository=doc_repo,
        download_gateway=download_gw,
        process_gateway=process_gw,
        inconsistency_repository=inconsistency_repo,
    )

    health_check_service = DataHealthCheckService(
        page_repository=page_repo,
        document_repository=doc_repo,
        download_gateway=download_gw,
        inconsistency_repository=inconsistency_repo,
    )

    cli = DocumentIngesterCLI(
        ingestion_service=ingestion_service,
        health_check_service=health_check_service,
    )
    return cli, db


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point CLI: argparse + roteamento para DocumentIngesterCLI."""
    import argparse

    logging.basicConfig(
        level=logging.WARNING,  # CLI mostra output limpo; logs apenas em WARNING+
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Pipeline de ingestão de documentos — Transparência TRE-PI",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    # Comandos mutuamente exclusivos (principal ação)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--run",
        action="store_true",
        help="Processa todos os documentos pendentes",
    )
    group.add_argument(
        "--reprocess",
        action="store_true",
        help="Reprocessa documentos com erro",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Exibe estatísticas do pipeline",
    )
    group.add_argument(
        "--url",
        metavar="URL",
        help="Processa um documento específico",
    )
    group.add_argument(
        "--check",
        action="store_true",
        help="[V2] Executa health check completo",
    )
    group.add_argument(
        "--inconsistencies",
        action="store_true",
        help="[V2] Lista inconsistências pendentes",
    )

    # Opções adicionais
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        metavar="N",
        help="Nível de concorrência para --run (padrão: 3)",
    )
    parser.add_argument(
        "--severity",
        choices=["critical", "warning", "info"],
        help="Filtrar inconsistências por severidade",
    )
    parser.add_argument(
        "--type",
        dest="inconsistency_type",
        metavar="TYPE",
        help="Filtrar inconsistências por tipo (ex: broken_link, document_not_found)",
    )
    parser.add_argument(
        "--resource-type",
        dest="resource_type",
        choices=["page", "document", "link", "chunk"],
        help="Filtrar inconsistências por tipo de recurso",
    )

    args = parser.parse_args()

    async def _run() -> None:
        cli, db = await _build_cli_async()
        try:
            if args.run:
                await cli.run(concurrency=args.concurrency)
            elif args.reprocess:
                await cli.reprocess()
            elif args.status:
                await cli.status()
            elif args.url:
                await cli.single(args.url)
            elif args.check:
                await cli.check()
            elif args.inconsistencies:
                await cli.inconsistencies(
                    severity=args.severity,
                    resource_type=getattr(args, "resource_type", None),
                    inconsistency_type=args.inconsistency_type,
                )
        finally:
            await db.__aexit__(None, None, None)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário.")
        sys.exit(0)


if __name__ == "__main__":
    main()
