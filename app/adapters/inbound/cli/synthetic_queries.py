"""CLI adapter: Geração de perguntas sintéticas (Query Augmentation — Fase 1).

Uso:
    # Gerar para todos os chunks pendentes (page_chunk + chunk)
    python -m app.adapters.inbound.cli.synthetic_queries --generate

    # Gerar apenas para um tipo específico
    python -m app.adapters.inbound.cli.synthetic_queries --generate --source-type page_chunk

    # Status
    python -m app.adapters.inbound.cli.synthetic_queries --status

    # Regenerar para um chunk específico
    python -m app.adapters.inbound.cli.synthetic_queries --regenerate \\
        --source-type page_chunk --source-id 42
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone

from app.adapters.inbound.cli.progress import ProgressBar, format_duration, print_summary
from app.domain.value_objects.synthetic_query import GenerationResult

logger = logging.getLogger(__name__)


# ── SyntheticQueriesCLI ───────────────────────────────────────────────────────


class SyntheticQueriesCLI:
    """Adapter CLI para o pipeline de geração de perguntas sintéticas."""

    def __init__(self, service: object) -> None:
        self._service = service

    async def generate(
        self,
        batch_size: int = 50,
        source_types: list[str] | None = None,
    ) -> None:
        """Gera perguntas sintéticas para chunks pendentes."""
        types_label = ", ".join(source_types) if source_types else "page_chunk + chunk"
        sys.stderr.write(f"\n  Gerando perguntas sintéticas ({types_label})\n\n")
        sys.stderr.flush()

        start = datetime.now(tz=timezone.utc)

        result: GenerationResult = await self._service.generate_for_pending_chunks(
            batch_size=batch_size,
            source_types=source_types,
        )

        duration = (datetime.now(tz=timezone.utc) - start).total_seconds()

        print_summary(
            "Perguntas Sintéticas — Resumo",
            {
                "Chunks processados": result.chunks_processed,
                "Perguntas geradas": result.questions_generated,
                "Embeddings criados": result.embeddings_created,
                "Erros": result.errors,
                "Já cobertos (skip)": result.skipped,
            },
            duration,
        )

    async def regenerate(self, source_type: str, source_id: int) -> None:
        """Regenera perguntas para um chunk específico."""
        print(f"\nRegenerando perguntas para {source_type}/{source_id}...")
        count: int = await self._service.regenerate_for_chunk(source_type, source_id)
        if count:
            print(f"OK — {count} perguntas geradas.")
        else:
            print("AVISO — nenhuma pergunta gerada (chunk não encontrado ou vazio).")

    async def status(self) -> None:
        """Exibe status atual da geração."""
        info: dict = await self._service.get_status()

        print("\n=== Status — Perguntas Sintéticas ===")
        print(f"Total de perguntas: {info.get('total_questions', 0)}")

        by_type = info.get("questions_by_source_type", {})
        if by_type:
            print("\nPor tipo de fonte:")
            for src_type, count in sorted(by_type.items()):
                print(f"  {src_type:<20} {count}")


# ── Factory ───────────────────────────────────────────────────────────────────


async def _build_service_async() -> tuple[SyntheticQueriesCLI, object]:
    """Constrói o serviço com todas as dependências injetadas."""
    from app.adapters.outbound.postgres.connection import DatabasePool, get_pool
    from app.adapters.outbound.postgres.embedding_repo import PostgresEmbeddingRepository
    from app.adapters.outbound.postgres.synthetic_query_generator_service import (
        PostgresSyntheticQueryGeneratorService,
    )
    from app.adapters.outbound.postgres.synthetic_query_repo import (
        PostgresSyntheticQueryRepository,
    )
    from app.adapters.outbound.vertex_ai.embedding_gateway import VertexAIEmbeddingGateway
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
    embedding_gw = VertexAIEmbeddingGateway(
        project_id=settings.vertex_project_id,
        location=settings.vertex_location,
        model_name=settings.vertex_embedding_model,
        output_dimensionality=settings.embedding_dimensions,
    )
    sq_repo = PostgresSyntheticQueryRepository(pool)
    emb_repo = PostgresEmbeddingRepository(pool)

    service = PostgresSyntheticQueryGeneratorService(
        pool=pool,
        llm_gateway=llm,
        embedding_gateway=embedding_gw,
        synthetic_query_repo=sq_repo,
        embedding_repo=emb_repo,
        model_name=settings.vertex_model,
    )

    cli = SyntheticQueriesCLI(service)
    return cli, db


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    """Entry point CLI: argparse + roteamento para SyntheticQueriesCLI."""
    import argparse

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Geração de perguntas sintéticas (Query Augmentation) — Cristal",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--generate",
        action="store_true",
        help="Gera perguntas para todos os chunks pendentes",
    )
    group.add_argument(
        "--regenerate",
        action="store_true",
        help="Regenera perguntas para um chunk específico (requer --source-type e --source-id)",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Exibe status da geração",
    )

    parser.add_argument(
        "--source-type",
        choices=["page_chunk", "chunk"],
        help="Tipo de fonte (para --generate filtra; para --regenerate é obrigatório)",
    )
    parser.add_argument(
        "--source-id",
        type=int,
        metavar="ID",
        help="ID do chunk para --regenerate",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=50,
        metavar="N",
        help="Chunks processados por execução (padrão: 50)",
    )

    args = parser.parse_args()

    if args.regenerate and (args.source_type is None or args.source_id is None):
        parser.error("--regenerate requer --source-type e --source-id")

    async def _run() -> None:
        cli, db = await _build_service_async()
        try:
            if args.generate:
                source_types = [args.source_type] if args.source_type else None
                await cli.generate(batch_size=args.batch_size, source_types=source_types)
            elif args.regenerate:
                await cli.regenerate(
                    source_type=args.source_type,
                    source_id=args.source_id,
                )
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
