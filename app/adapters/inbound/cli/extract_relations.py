"""CLI: extração de relações entre documentos (Fase 6 NOVO_RAG).

Uso:
  python -m app.adapters.inbound.cli.extract_relations --extract [--strategy entity|link|all]
  python -m app.adapters.inbound.cli.extract_relations --status
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os

import asyncpg

from app.adapters.outbound.postgres.relation_extractor_service import (
    PostgresRelationExtractorService,
)
from app.domain.value_objects.document_relation import RelationExtractionResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def _print_result(result: RelationExtractionResult) -> None:
    print("\n=== Resultado da Extração ===")
    print(f"  Páginas processadas:   {result.pages_processed:>8,}")
    print(f"  Relações encontradas:  {result.relations_found:>8,}")
    print(f"  Relações inseridas:    {result.relations_inserted:>8,}")
    print(f"  Puladas (cobertas):    {result.skipped:>8,}")
    print(f"  Erros:                 {result.errors:>8,}")
    if result.strategy_counts:
        print("\n  Por estratégia:")
        for strategy, cnt in sorted(result.strategy_counts.items()):
            print(f"    {strategy:<10} {cnt:>8,}")
    print()


async def _run(args: argparse.Namespace) -> None:
    dsn = os.environ.get("DATABASE_URL", "postgresql://cristal:cristal@localhost:5432/cristal")
    pool = await asyncpg.create_pool(dsn, min_size=2, max_size=5)
    service = PostgresRelationExtractorService(pool)

    try:
        if args.status:
            counts = await service.get_status()
            print("\n=== Status das Relações ===")
            if not counts:
                print("  Nenhuma relação extraída ainda.")
            else:
                for key, cnt in sorted(counts.items()):
                    print(f"  {key:<30} {cnt:>8,}")
            print()
            return

        combined = RelationExtractionResult()
        strategies = args.strategy if args.strategy != "all" else ["entity", "link"]

        for strategy in (strategies if isinstance(strategies, list) else [strategies]):
            print(f"Extraindo relações [{strategy}] ...")
            if strategy == "entity":
                result = await service.extract_entity_relations(
                    batch_size=args.batch_size,
                    skip_covered=not args.force,
                )
            elif strategy == "link":
                result = await service.extract_link_relations(
                    batch_size=args.batch_size,
                    skip_covered=not args.force,
                )
            else:
                logger.warning("Estratégia desconhecida: %s", strategy)
                continue

            combined.pages_processed   += result.pages_processed
            combined.relations_found   += result.relations_found
            combined.relations_inserted += result.relations_inserted
            combined.skipped           += result.skipped
            combined.errors            += result.errors
            for k, v in result.strategy_counts.items():
                combined.strategy_counts[k] = combined.strategy_counts.get(k, 0) + v

        _print_result(combined)

    finally:
        await pool.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extração de relações entre documentos (Fase 6 NOVO_RAG)"
    )
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--extract", action="store_true", help="Extrai e persiste relações")
    action.add_argument("--status",  action="store_true", help="Exibe totais por estratégia")

    parser.add_argument(
        "--strategy",
        choices=["entity", "link", "all"],
        default="all",
        help="Estratégia de extração (padrão: all)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=200,
        dest="batch_size",
        help="Páginas por lote (padrão: 200)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocessa páginas já cobertas",
    )

    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
