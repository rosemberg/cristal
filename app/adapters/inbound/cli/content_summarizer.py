"""CLI adapter: Sumarização e Indexação Multinível (Fase 2 NOVO_RAG).

Uso:
    # Gerar sumários LLM para todas as páginas pendentes
    python -m app.adapters.inbound.cli.content_summarizer --generate

    # Gerar sumários de seções para documentos longos pendentes
    python -m app.adapters.inbound.cli.content_summarizer --generate --type sections

    # Gerar ambos em sequência
    python -m app.adapters.inbound.cli.content_summarizer --generate --type all

    # Status
    python -m app.adapters.inbound.cli.content_summarizer --status

    # Regenerar para uma página específica
    python -m app.adapters.inbound.cli.content_summarizer --regenerate --page-id 42
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

from app.adapters.inbound.cli.progress import print_summary

logger = logging.getLogger(__name__)


# ── ContentSummarizerCLI ──────────────────────────────────────────────────────


class ContentSummarizerCLI:
    """Adapter CLI para o pipeline de sumarização de conteúdo."""

    def __init__(self, service: object) -> None:
        self._service = service

    async def generate_pages(self, batch_size: int = 100) -> None:
        """Gera sumários LLM para páginas pendentes."""
        sys.stderr.write("\n  Gerando sumários de páginas (Fase 2 — page_summary)\n\n")
        sys.stderr.flush()

        start = datetime.now(tz=timezone.utc)
        result = await self._service.generate_for_pending_pages(batch_size=batch_size)
        duration = (datetime.now(tz=timezone.utc) - start).total_seconds()

        print_summary(
            "Sumários de Páginas — Resumo",
            {
                "Páginas processadas": result.pages_processed,
                "Sumários gerados": result.summaries_generated,
                "Embeddings criados": result.embeddings_created,
                "Erros": result.errors,
                "Já cobertos (skip)": result.skipped,
            },
            duration,
        )

    async def generate_sections(self, batch_size: int = 20) -> None:
        """Gera sumários de seções para documentos longos pendentes."""
        sys.stderr.write("\n  Gerando sumários de seções (Fase 2 — section_summary)\n\n")
        sys.stderr.flush()

        start = datetime.now(tz=timezone.utc)
        result = await self._service.generate_section_summaries_for_pending_docs(
            batch_size=batch_size
        )
        duration = (datetime.now(tz=timezone.utc) - start).total_seconds()

        print_summary(
            "Sumários de Seções — Resumo",
            {
                "Documentos processados": result.pages_processed,
                "Seções geradas": result.summaries_generated,
                "Embeddings criados": result.embeddings_created,
                "Erros": result.errors,
                "Já cobertos (skip)": result.skipped,
            },
            duration,
        )

    async def regenerate(self, page_id: int) -> None:
        """Regenera sumário para uma página específica."""
        print(f"\nRegenerando sumário para página {page_id}...")
        count: int = await self._service.regenerate_for_page(page_id)
        if count:
            print(f"OK — sumário gerado e {count} embedding(s) criado(s).")
        else:
            print("AVISO — sumário não gerado (página não encontrada ou sem conteúdo).")

    async def status(self) -> None:
        """Exibe status atual da sumarização."""
        info: dict = await self._service.get_status()

        print("\n=== Status — Sumarização de Conteúdo (Fase 2) ===")

        pages_done = info.get("pages_with_summary_embedding", 0)
        pages_total = info.get("total_pages", 0)
        pages_pct = (pages_done / pages_total * 100) if pages_total else 0.0
        print(f"\nPáginas:")
        print(f"  Com sumário LLM : {pages_done} / {pages_total}  ({pages_pct:.1f}%)")
        print(f"  Pendentes       : {info.get('pages_pending', 0)}")

        docs_done = info.get("documents_with_section_summaries", 0)
        docs_total = info.get("total_long_documents", 0)
        docs_pct = (docs_done / docs_total * 100) if docs_total else 0.0
        print(f"\nDocumentos longos (seções):")
        print(f"  Com sumário     : {docs_done} / {docs_total}  ({docs_pct:.1f}%)")
        print(f"  Pendentes       : {info.get('documents_pending', 0)}")


# ── Factory ───────────────────────────────────────────────────────────────────


async def _build_service_async() -> tuple[ContentSummarizerCLI, object]:
    """Constrói o serviço com todas as dependências injetadas."""
    from app.adapters.outbound.postgres.connection import DatabasePool, get_pool
    from app.adapters.outbound.postgres.content_summarizer_service import (
        PostgresContentSummarizerService,
    )
    from app.adapters.outbound.postgres.embedding_repo import PostgresEmbeddingRepository
    from app.adapters.outbound.vertex_ai.embedding_gateway import VertexEmbeddingGateway
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
    embedding_gw = VertexEmbeddingGateway(
        project_id=settings.vertex_project_id,
        location=settings.vertex_embedding_location,
        model_name=settings.vertex_embedding_model,
        output_dimensionality=settings.embedding_dimensions,
    )
    emb_repo = PostgresEmbeddingRepository(pool)

    service = PostgresContentSummarizerService(
        pool=pool,
        llm_gateway=llm,
        embedding_gateway=embedding_gw,
        embedding_repo=emb_repo,
        model_name=settings.vertex_model,
    )

    cli = ContentSummarizerCLI(service)
    return cli, db


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point CLI: argparse + roteamento para ContentSummarizerCLI."""
    import argparse

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Sumarização e Indexação Multinível (Fase 2 NOVO_RAG) — Cristal",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--generate",
        action="store_true",
        help="Gera sumários para páginas/seções pendentes",
    )
    group.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenera sumário para uma página específica (requer --page-id)",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Exibe status da sumarização",
    )

    parser.add_argument(
        "--type",
        choices=["pages", "sections", "all"],
        default="pages",
        help="Tipo de sumário a gerar: pages (padrão), sections ou all",
    )
    parser.add_argument(
        "--page-id",
        type=int,
        metavar="ID",
        help="ID da página para --regenerate",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        metavar="N",
        help="Páginas processadas por execução (padrão: 100)",
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

    if args.regenerate and args.page_id is None:
        parser.error("--regenerate requer --page-id")

    async def _run() -> None:
        cli, db = await _build_service_async()
        try:
            if args.generate:
                if args.type in ("pages", "all"):
                    await cli.generate_pages(batch_size=args.batch_size)
                if args.type in ("sections", "all"):
                    await cli.generate_sections(batch_size=args.batch_size)
            elif args.regenerate:
                await cli.regenerate(page_id=args.page_id)
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
