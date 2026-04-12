"""CLI adapter: Enriquecimento de Metadados Estruturados (Fase 3 NOVO_RAG).

Uso:
    # Etapa A (regex) + Etapa B (LLM) para páginas sem tags
    python -m app.adapters.inbound.cli.metadata_enricher --enrich

    # Apenas regex (re-extrai entidades de todas as páginas, sem LLM)
    python -m app.adapters.inbound.cli.metadata_enricher --enrich --step regex

    # Apenas classificação LLM para páginas sem tags
    python -m app.adapters.inbound.cli.metadata_enricher --enrich --step llm

    # Status
    python -m app.adapters.inbound.cli.metadata_enricher --status

    # Re-enriquecer uma página específica
    python -m app.adapters.inbound.cli.metadata_enricher --reenrich --page-id 42
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

from app.adapters.inbound.cli.progress import print_summary

logger = logging.getLogger(__name__)


# ── MetadataEnricherCLI ───────────────────────────────────────────────────────


class MetadataEnricherCLI:
    """Adapter CLI para o pipeline de enriquecimento de metadados."""

    def __init__(self, service: object) -> None:
        self._service = service

    async def enrich(self, batch_size: int = 50, step: str = "all") -> None:
        """Executa enriquecimento conforme o step: all | regex | llm."""
        labels = {
            "all": "Etapa A (regex) + Etapa B (LLM)",
            "regex": "Etapa A — regex NER (todas as páginas)",
            "llm": "Etapa B — classificação LLM (páginas sem tags)",
        }
        sys.stderr.write(
            f"\n  Enriquecimento de Metadados — {labels.get(step, step)}\n\n"
        )
        sys.stderr.flush()

        start = datetime.now(tz=timezone.utc)

        if step == "regex":
            result = await self._service.enrich_all_pages_regex(batch_size=batch_size)
        elif step == "llm":
            result = await self._service.enrich_pending_pages_llm(batch_size=batch_size)
        else:
            result = await self._service.enrich_pending_pages(batch_size=batch_size)

        duration = (datetime.now(tz=timezone.utc) - start).total_seconds()

        print_summary(
            f"Metadados ({step}) — Resumo",
            {
                "Páginas processadas": result.pages_processed,
                "Entidades extraídas": result.entities_extracted,
                "Tags geradas": result.tags_extracted,
                "Erros": result.errors,
                "Já cobertos (skip)": result.skipped,
            },
            duration,
        )

    async def reenrich(self, page_id: int) -> None:
        """Re-enriquece uma página específica."""
        print(f"\nRe-enriquecendo página {page_id}...")
        entities, tags = await self._service.reenrich_page(page_id)
        if entities or tags:
            print(f"OK — {entities} entidade(s) e {tags} tag(s) geradas.")
        else:
            print("AVISO — nada gerado (página não encontrada ou sem conteúdo).")

    async def status(self) -> None:
        """Exibe status atual do enriquecimento."""
        info: dict = await self._service.get_status()

        pages_done = info.get("pages_with_tags", 0)
        pages_total = info.get("total_pages", 0)
        pages_pct = (pages_done / pages_total * 100) if pages_total else 0.0

        print("\n=== Status — Enriquecimento de Metadados (Fase 3) ===")
        print(f"\nPáginas:")
        print(f"  Com tags LLM  : {pages_done} / {pages_total}  ({pages_pct:.1f}%)")
        print(f"  Pendentes     : {info.get('pages_pending', 0)}")
        print(f"\nEntidades:")
        print(f"  Total         : {info.get('total_entities', 0)}")

        entities_by_type = info.get("entities_by_type", {})
        if entities_by_type:
            for etype, count in sorted(entities_by_type.items(), key=lambda x: -x[1]):
                print(f"  {etype:<22} {count}")

        print(f"\nTags:")
        print(f"  Total         : {info.get('total_tags', 0)}")

        tags_by_name = info.get("tags_by_name", {})
        if tags_by_name:
            for tag, count in sorted(tags_by_name.items(), key=lambda x: -x[1]):
                print(f"  {tag:<22} {count}")


# ── Factory ───────────────────────────────────────────────────────────────────


async def _build_service_async() -> tuple[MetadataEnricherCLI, object]:
    """Constrói o serviço com todas as dependências injetadas."""
    from app.adapters.outbound.postgres.connection import DatabasePool, get_pool
    from app.adapters.outbound.postgres.metadata_enricher_service import (
        PostgresMetadataEnricherService,
    )
    from app.adapters.outbound.postgres.metadata_repo import PostgresMetadataRepository
    from app.adapters.outbound.vertex_ai.gateway import VertexAIGateway
    from app.config.settings import get_settings

    settings = get_settings()

    db = DatabasePool(settings)
    await db.__aenter__()
    pool = get_pool(db)

    llm = VertexAIGateway(
        project_id=settings.vertex_project_id,
        location=settings.vertex_location,
        model_name=settings.vertex_model,
    )
    metadata_repo = PostgresMetadataRepository(pool)

    service = PostgresMetadataEnricherService(
        pool=pool,
        llm_gateway=llm,
        metadata_repo=metadata_repo,
        model_name=settings.vertex_model,
        llm_batch_size=3,
    )

    cli = MetadataEnricherCLI(service)
    return cli, db


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point CLI: argparse + roteamento para MetadataEnricherCLI."""
    import argparse

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Enriquecimento de Metadados Estruturados (Fase 3 NOVO_RAG) — Cristal",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--enrich",
        action="store_true",
        help="Enriquece páginas pendentes (regex + LLM por padrão)",
    )
    group.add_argument(
        "--reenrich",
        action="store_true",
        help="Re-enriquece uma página específica (requer --page-id)",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Exibe status do enriquecimento",
    )

    parser.add_argument(
        "--step",
        choices=["all", "regex", "llm"],
        default="all",
        help=(
            "Etapa a executar (padrão: all):\n"
            "  all   — regex NER + classificação LLM (páginas sem tags)\n"
            "  regex — apenas regex em TODAS as páginas (sem LLM)\n"
            "  llm   — apenas LLM nas páginas sem tags"
        ),
    )
    parser.add_argument(
        "--page-id",
        type=int,
        metavar="ID",
        help="ID da página para --reenrich",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        metavar="N",
        help="Páginas processadas por iteração (padrão: 50; regex usa 200)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Exibe logs detalhados (DEBUG)",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logging.getLogger("app").setLevel(logging.DEBUG)

    if args.reenrich and args.page_id is None:
        parser.error("--reenrich requer --page-id")

    async def _run() -> None:
        cli, db = await _build_service_async()
        try:
            if args.enrich:
                batch = 200 if args.step == "regex" else args.batch_size
                await cli.enrich(batch_size=batch, step=args.step)
            elif args.reenrich:
                await cli.reenrich(page_id=args.page_id)
            elif args.status:
                await cli.status()
        finally:
            await db.__aexit__(None, None, None)

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário.")
        sys.exit(0)


if __name__ == "__main__":
    main()
