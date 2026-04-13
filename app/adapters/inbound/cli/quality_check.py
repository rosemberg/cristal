"""CLI: pipeline de qualidade de chunks (Fase 5 NOVO_RAG).

Uso:
  python -m app.adapters.inbound.cli.quality_check --score [--target docs|pages|all]
  python -m app.adapters.inbound.cli.quality_check --quarantine [--target docs|pages|all]
  python -m app.adapters.inbound.cli.quality_check --deduplicate [--target docs|pages|all]
  python -m app.adapters.inbound.cli.quality_check --report
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.adapters.outbound.postgres.chunk_quality_service import PostgresChunkQualityService
from app.adapters.outbound.postgres.connection import DatabasePool, get_pool
from app.config.settings import get_settings
from app.domain.value_objects.chunk_quality import QualityReport

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _tables_for_target(target: str) -> list[str]:
    if target == "docs":
        return ["document_chunks"]
    if target == "pages":
        return ["page_chunks"]
    return ["document_chunks", "page_chunks"]


def _print_report(report: QualityReport) -> None:
    print("\n=== Relatório de Qualidade ===")
    print(f"  Chunks pontuados:      {report.chunks_scored:>8,}")
    print(f"  Chunks em quarentena:  {report.chunks_quarantined:>8,}")
    print(f"  Chunks deduplicados:   {report.chunks_deduplicated:>8,}")
    print(f"  Erros:                 {report.errors:>8,}")

    if report.score_distribution:
        print("\n  Distribuição de scores:")
        for bucket in sorted(report.score_distribution):
            cnt = report.score_distribution[bucket]
            bar = "█" * min(cnt // 10, 40)
            print(f"    {bucket}  {cnt:>6,}  {bar}")

    if report.flag_counts:
        print("\n  Flags mais comuns:")
        for flag, cnt in sorted(report.flag_counts.items(), key=lambda x: -x[1])[:10]:
            print(f"    {flag:<25} {cnt:>6,}")
    print()


async def _run(args: argparse.Namespace) -> None:
    settings = get_settings()
    db = DatabasePool(settings)
    await db.__aenter__()
    pool = get_pool(db)
    service = PostgresChunkQualityService(pool)
    tables = _tables_for_target(args.target)

    try:
        if args.report:
            report = await service.get_report()
            _print_report(report)
            return

        combined = QualityReport()

        if args.score:
            for table in tables:
                print(f"Pontuando {table} ...")
                report = await service.score_pending(table, batch_size=args.batch_size)
                combined.chunks_scored      += report.chunks_scored
                combined.chunks_quarantined += report.chunks_quarantined
                combined.errors             += report.errors
                for k, v in report.flag_counts.items():
                    combined.flag_counts[k] = combined.flag_counts.get(k, 0) + v
                for k, v in report.score_distribution.items():
                    combined.score_distribution[k] = combined.score_distribution.get(k, 0) + v
            _print_report(combined)

        if args.deduplicate:
            combined_dup = QualityReport()
            for table in tables:
                print(f"Deduplicando {table} ...")
                report = await service.deduplicate(table, batch_size=args.batch_size)
                combined_dup.chunks_deduplicated += report.chunks_deduplicated
            print(f"\n  Duplicatas quarentenadas: {combined_dup.chunks_deduplicated:,}")

    finally:
        await db.__aexit__(None, None, None)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pipeline de qualidade de chunks (Fase 5 NOVO_RAG)"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--score",       action="store_true", help="Pontua chunks sem score")
    action.add_argument("--deduplicate", action="store_true", help="Detecta e quarentena duplicatas")
    action.add_argument("--report",      action="store_true", help="Exibe relatório agregado")

    parser.add_argument(
        "--target",
        choices=["docs", "pages", "all"],
        default="all",
        help="Tabela alvo (padrão: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        dest="batch_size",
        help="Chunks por lote (padrão: 500)",
    )

    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
