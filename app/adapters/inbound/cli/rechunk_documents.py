"""CLI adapter: Rechunking Semântico de Documentos e Páginas (Fase 4 NOVO_RAG).

Migra os chunks existentes do TextChunker (version=1) para o SemanticChunker
(version=2) usando o full_text e main_content já armazenados no banco.

Uso:
    # Simula rechunking sem gravar nada
    python -m app.adapters.inbound.cli.rechunk_documents --dry-run

    # Rechunka todos os documentos (document_chunks)
    python -m app.adapters.inbound.cli.rechunk_documents --execute

    # Rechunka apenas as páginas (page_chunks)
    python -m app.adapters.inbound.cli.rechunk_documents --execute --target pages

    # Rechunka tudo (docs + páginas)
    python -m app.adapters.inbound.cli.rechunk_documents --execute --target all

    # Limpa embeddings órfãos e regera para os novos chunks
    python -m app.adapters.inbound.cli.rechunk_documents --reembed

    # Status: distribuição de versões de chunks
    python -m app.adapters.inbound.cli.rechunk_documents --status
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone

import asyncpg

from app.adapters.inbound.cli.progress import ProgressBar, format_duration, print_summary
from app.adapters.outbound.document_processor.semantic_chunker import SemanticChunker

logger = logging.getLogger(__name__)


# ─── Result dataclass ─────────────────────────────────────────────────────────


@dataclass
class RechunkResult:
    processed: int = 0
    chunks_before: int = 0
    chunks_after: int = 0
    errors: int = 0
    skipped: int = 0


# ─── Core rechunking logic ────────────────────────────────────────────────────


async def rechunk_documents(
    pool: asyncpg.Pool,
    chunker: SemanticChunker,
    dry_run: bool = False,
) -> RechunkResult:
    """Rechunka document_contents → document_chunks usando SemanticChunker."""
    result = RechunkResult()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT dc.document_url, dc.full_text,
                   (SELECT COUNT(*) FROM document_chunks WHERE document_url = dc.document_url) AS chunk_count
            FROM document_contents dc
            WHERE dc.processing_status = 'done'
              AND dc.full_text IS NOT NULL
              AND LENGTH(dc.full_text) > 100
            ORDER BY dc.document_url
            """
        )

    total = len(rows)
    if total == 0:
        sys.stderr.write("  Nenhum documento com full_text encontrado.\n")
        return result

    bar = ProgressBar(total, prefix="Rechunk docs")

    for row in rows:
        url = row["document_url"]
        full_text = row["full_text"]
        old_count = int(row["chunk_count"])
        result.chunks_before += old_count

        try:
            new_chunks = chunker.chunk_plain_text(text=full_text, document_url=url)

            if not dry_run:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "DELETE FROM document_chunks WHERE document_url = $1", url
                        )
                        if new_chunks:
                            await conn.executemany(
                                """
                                INSERT INTO document_chunks
                                    (document_url, chunk_index, chunk_text,
                                     section_title, page_number, token_count,
                                     version, has_table, parent_chunk_id)
                                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                                """,
                                [
                                    (
                                        url,
                                        c.chunk_index,
                                        c.text,
                                        c.section_title,
                                        c.page_number,
                                        c.token_count,
                                        c.version,
                                        c.has_table,
                                        c.parent_chunk_id,
                                    )
                                    for c in new_chunks
                                ],
                            )

            result.chunks_after += len(new_chunks)
            result.processed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rechunk: erro em %s: %s", url, exc)
            result.errors += 1

        bar.update(current_item=url.split("/")[-1])

    bar.finish()
    return result


async def rechunk_pages(
    pool: asyncpg.Pool,
    chunker: SemanticChunker,
    dry_run: bool = False,
) -> RechunkResult:
    """Rechunka pages.main_content → page_chunks usando SemanticChunker (HTML)."""
    result = RechunkResult()

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.id AS page_id, p.url, p.main_content, p.breadcrumb,
                   (SELECT COUNT(*) FROM page_chunks WHERE page_id = p.id) AS chunk_count
            FROM pages p
            WHERE p.main_content IS NOT NULL
              AND LENGTH(p.main_content) > 100
            ORDER BY p.id
            """
        )

    total = len(rows)
    if total == 0:
        sys.stderr.write("  Nenhuma página com main_content encontrada.\n")
        return result

    bar = ProgressBar(total, prefix="Rechunk pages")

    for row in rows:
        page_id = row["page_id"]
        url = row["url"]
        html = row["main_content"]
        breadcrumb_list = list(row["breadcrumb"]) if row["breadcrumb"] else []
        breadcrumb = " > ".join(breadcrumb_list) if breadcrumb_list else None
        old_count = int(row["chunk_count"])
        result.chunks_before += old_count

        try:
            new_chunks = chunker.chunk_html(
                html=html,
                document_url=url,
                breadcrumb=breadcrumb,
            )

            if not dry_run:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        await conn.execute(
                            "DELETE FROM page_chunks WHERE page_id = $1", page_id
                        )
                        if new_chunks:
                            await conn.executemany(
                                """
                                INSERT INTO page_chunks
                                    (page_id, page_url, chunk_index, chunk_text,
                                     token_count, version)
                                VALUES ($1, $2, $3, $4, $5, $6)
                                """,
                                [
                                    (
                                        page_id,
                                        url,
                                        c.chunk_index,
                                        c.text,
                                        c.token_count,
                                        c.version,
                                    )
                                    for c in new_chunks
                                ],
                            )

            result.chunks_after += len(new_chunks)
            result.processed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rechunk: erro na página %s: %s", url, exc)
            result.errors += 1

        bar.update(current_item=url.split("/")[-1])

    bar.finish()
    return result


async def cleanup_orphaned_embeddings(pool: asyncpg.Pool) -> int:
    """Remove embeddings source_type='chunk' cujo source_id não existe mais."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM embeddings
            WHERE source_type = 'chunk'
              AND source_id NOT IN (SELECT id FROM document_chunks)
            """
        )
    # asyncpg retorna "DELETE N"
    deleted = int(result.split()[-1])
    logger.info("Embeddings órfãos removidos: %d", deleted)
    return deleted


async def cleanup_orphaned_page_chunk_embeddings(pool: asyncpg.Pool) -> int:
    """Remove embeddings source_type='page_chunk' cujo source_id não existe mais."""
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            DELETE FROM embeddings
            WHERE source_type = 'page_chunk'
              AND source_id NOT IN (SELECT id FROM page_chunks)
            """
        )
    deleted = int(result.split()[-1])
    logger.info("Embeddings órfãos de page_chunk removidos: %d", deleted)
    return deleted


# ─── CLI ─────────────────────────────────────────────────────────────────────


class RechunkCLI:
    """Adapter CLI para o pipeline de rechunking semântico."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._chunker = SemanticChunker()

    async def dry_run(self, target: str = "docs") -> None:
        """Simula rechunking e mostra estatísticas sem gravar."""
        sys.stderr.write("\n  [DRY-RUN] Simulando rechunking semântico...\n\n")
        start = datetime.now(tz=timezone.utc)

        result_docs = RechunkResult()
        result_pages = RechunkResult()

        if target in ("docs", "all"):
            sys.stderr.write("  Analisando document_chunks...\n")
            result_docs = await rechunk_documents(self._pool, self._chunker, dry_run=True)

        if target in ("pages", "all"):
            sys.stderr.write("  Analisando page_chunks...\n")
            result_pages = await rechunk_pages(self._pool, self._chunker, dry_run=True)

        duration = (datetime.now(tz=timezone.utc) - start).total_seconds()

        metrics: dict[str, object] = {}
        if target in ("docs", "all"):
            metrics["Documentos analisados"] = result_docs.processed + result_docs.errors
            metrics["Chunks atuais (docs)"] = result_docs.chunks_before
            metrics["Chunks após rechunk (docs)"] = result_docs.chunks_after
        if target in ("pages", "all"):
            metrics["Páginas analisadas"] = result_pages.processed + result_pages.errors
            metrics["Chunks atuais (pages)"] = result_pages.chunks_before
            metrics["Chunks após rechunk (pages)"] = result_pages.chunks_after

        print_summary("[DRY-RUN] Previsão de Rechunking", metrics, duration)

    async def execute(self, target: str = "docs") -> None:
        """Executa rechunking semântico (substitui chunks existentes)."""
        sys.stderr.write("\n  Rechunking Semântico — Fase 4 NOVO_RAG\n\n")
        start = datetime.now(tz=timezone.utc)

        result_docs = RechunkResult()
        result_pages = RechunkResult()

        if target in ("docs", "all"):
            sys.stderr.write("  Rechunkando documentos...\n")
            result_docs = await rechunk_documents(self._pool, self._chunker)

        if target in ("pages", "all"):
            sys.stderr.write("  Rechunkando páginas (HTML)...\n")
            result_pages = await rechunk_pages(self._pool, self._chunker)

        duration = (datetime.now(tz=timezone.utc) - start).total_seconds()

        metrics: dict[str, object] = {}
        if target in ("docs", "all"):
            metrics["Documentos processados"] = result_docs.processed
            metrics["Chunks antes (docs)"] = result_docs.chunks_before
            metrics["Chunks depois (docs)"] = result_docs.chunks_after
            metrics["Erros (docs)"] = result_docs.errors
        if target in ("pages", "all"):
            metrics["Páginas processadas"] = result_pages.processed
            metrics["Chunks antes (pages)"] = result_pages.chunks_before
            metrics["Chunks depois (pages)"] = result_pages.chunks_after
            metrics["Erros (pages)"] = result_pages.errors

        print_summary("Rechunking Semântico — Resumo", metrics, duration)
        sys.stderr.write(
            "\n  Próximo passo: python -m app.adapters.inbound.cli.rechunk_documents --reembed\n\n"
        )

    async def reembed(self) -> None:
        """Limpa embeddings órfãos e regenera para os novos chunks."""
        from app.adapters.outbound.postgres.embedding_repo import PostgresEmbeddingRepository
        from app.adapters.outbound.vertex_ai.embedding_gateway import VertexEmbeddingGateway
        from app.domain.ports.outbound.embedding_repository import EmbeddingRecord
        from app.config.settings import get_settings

        settings = get_settings()
        emb_gw = VertexEmbeddingGateway(
            project_id=settings.vertex_project_id,
            location=settings.vertex_embedding_location,
            model_name=settings.vertex_embedding_model,
            output_dimensionality=settings.embedding_dimensions,
        )
        emb_repo = PostgresEmbeddingRepository(self._pool)

        sys.stderr.write("\n  Re-embedding de chunks (Fase 4)\n\n")
        start = datetime.now(tz=timezone.utc)

        # 1. Limpa embeddings órfãos
        sys.stderr.write("  Limpando embeddings órfãos...\n")
        del_docs = await cleanup_orphaned_embeddings(self._pool)
        del_pages = await cleanup_orphaned_page_chunk_embeddings(self._pool)
        sys.stderr.write(f"  Removidos: {del_docs} (chunk) + {del_pages} (page_chunk)\n\n")

        # 2. Busca chunks sem embedding
        async with self._pool.acquire() as conn:
            doc_rows = await conn.fetch(
                """
                SELECT dc.id, dc.chunk_text
                FROM document_chunks dc
                WHERE NOT EXISTS (
                    SELECT 1 FROM embeddings e
                    WHERE e.source_type = 'chunk' AND e.source_id = dc.id
                )
                  AND LENGTH(dc.chunk_text) > 10
                ORDER BY dc.id
                """
            )
            page_rows = await conn.fetch(
                """
                SELECT pc.id, pc.chunk_text
                FROM page_chunks pc
                WHERE NOT EXISTS (
                    SELECT 1 FROM embeddings e
                    WHERE e.source_type = 'page_chunk' AND e.source_id = pc.id
                )
                  AND LENGTH(pc.chunk_text) > 10
                ORDER BY pc.id
                """
            )

        total = len(doc_rows) + len(page_rows)
        sys.stderr.write(
            f"  Chunks sem embedding: {len(doc_rows)} docs + {len(page_rows)} pages = {total} total\n\n"
        )

        if total == 0:
            sys.stderr.write("  Nada a fazer.\n")
            return

        import hashlib

        def sha256(t: str) -> str:
            return hashlib.sha256(t.encode()).hexdigest()

        batch_size = 50
        emb_created = 0
        errors = 0
        bar = ProgressBar(total, prefix="Re-embed")

        # Processa document_chunks
        for i in range(0, len(doc_rows), batch_size):
            batch = doc_rows[i : i + batch_size]
            ids = [r["id"] for r in batch]
            texts = [r["chunk_text"] for r in batch]
            try:
                vectors = await emb_gw.embed_batch(texts, task_type="RETRIEVAL_DOCUMENT")
                records = [
                    EmbeddingRecord(
                        source_type="chunk",
                        source_id=cid,
                        embedding=vec,
                        source_text_hash=sha256(text),
                    )
                    for cid, vec, text in zip(ids, vectors, texts)
                ]
                await emb_repo.save_batch(records)
                emb_created += len(records)
            except Exception as exc:  # noqa: BLE001
                logger.warning("reembed: erro no batch chunk [%d:%d]: %s", i, i + len(batch), exc)
                errors += len(batch)
            for _ in batch:
                bar.update()

        # Processa page_chunks
        for i in range(0, len(page_rows), batch_size):
            batch = page_rows[i : i + batch_size]
            ids = [r["id"] for r in batch]
            texts = [r["chunk_text"] for r in batch]
            try:
                vectors = await emb_gw.embed_batch(texts, task_type="RETRIEVAL_DOCUMENT")
                records = [
                    EmbeddingRecord(
                        source_type="page_chunk",
                        source_id=cid,
                        embedding=vec,
                        source_text_hash=sha256(text),
                    )
                    for cid, vec, text in zip(ids, vectors, texts)
                ]
                await emb_repo.save_batch(records)
                emb_created += len(records)
            except Exception as exc:  # noqa: BLE001
                logger.warning("reembed: erro no batch page_chunk [%d:%d]: %s", i, i + len(batch), exc)
                errors += len(batch)
            for _ in batch:
                bar.update()

        bar.finish()
        duration = (datetime.now(tz=timezone.utc) - start).total_seconds()
        print_summary(
            "Re-embedding — Resumo",
            {
                "Embeddings criados": emb_created,
                "Erros": errors,
                "Órfãos removidos": del_docs + del_pages,
            },
            duration,
        )

    async def status(self) -> None:
        """Exibe distribuição de versões de chunks."""
        async with self._pool.acquire() as conn:
            doc_versions = await conn.fetch(
                "SELECT version, COUNT(*) AS n FROM document_chunks GROUP BY version ORDER BY version"
            )
            page_versions = await conn.fetch(
                "SELECT version, COUNT(*) AS n FROM page_chunks GROUP BY version ORDER BY version"
            )
            doc_total = await conn.fetchval("SELECT COUNT(*) FROM document_chunks")
            page_total = await conn.fetchval("SELECT COUNT(*) FROM page_chunks")

        print("\n=== Status — Chunking Semântico (Fase 4) ===")
        print(f"\ndocument_chunks ({doc_total} total):")
        for r in doc_versions:
            label = "TextChunker (legado)" if r["version"] == 1 else "SemanticChunker"
            print(f"  v{r['version']} ({label}): {r['n']}")

        print(f"\npage_chunks ({page_total} total):")
        for r in page_versions:
            label = "TextChunker (legado)" if r["version"] == 1 else "SemanticChunker"
            print(f"  v{r['version']} ({label}): {r['n']}")


# ─── Factory ─────────────────────────────────────────────────────────────────


async def _build_cli_async() -> tuple[RechunkCLI, object]:
    from app.adapters.outbound.postgres.connection import DatabasePool, get_pool
    from app.config.settings import get_settings

    settings = get_settings()
    db = DatabasePool(settings)
    await db.__aenter__()
    pool = get_pool(db)
    return RechunkCLI(pool), db


# ─── Entry point ─────────────────────────────────────────────────────────────


def main() -> None:
    import argparse

    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Rechunking Semântico (Fase 4 NOVO_RAG) — Cristal",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--dry-run",
        action="store_true",
        help="Simula rechunking sem gravar (mostra previsão de chunks)",
    )
    group.add_argument(
        "--execute",
        action="store_true",
        help="Executa rechunking (substitui chunks existentes por v2)",
    )
    group.add_argument(
        "--reembed",
        action="store_true",
        help="Limpa embeddings órfãos e regera para os novos chunks",
    )
    group.add_argument(
        "--status",
        action="store_true",
        help="Exibe distribuição de versões de chunks",
    )

    parser.add_argument(
        "--target",
        choices=["docs", "pages", "all"],
        default="docs",
        help=(
            "Alvo do rechunking (padrão: docs):\n"
            "  docs  — apenas document_chunks (PDFs)\n"
            "  pages — apenas page_chunks (HTML de páginas)\n"
            "  all   — documentos + páginas"
        ),
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

    async def _run() -> None:
        cli, db = await _build_cli_async()
        try:
            if args.dry_run:
                await cli.dry_run(target=args.target)
            elif args.execute:
                await cli.execute(target=args.target)
            elif args.reembed:
                await cli.reembed()
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
