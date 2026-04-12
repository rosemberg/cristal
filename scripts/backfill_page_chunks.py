#!/usr/bin/env python3
"""Script: backfill_page_chunks.py

Divide o main_content de páginas longas em chunks menores, indexa em FTS
e gera embeddings semânticos (source_type='page_chunk').

Problema resolvido: páginas com centenas de kilobytes (ex: lista de contratos,
servidores) têm um único embedding — o RAG não recupera dados além dos primeiros
tokens. Cada chunk individual recebe seu próprio embedding, permitindo busca
semântica precisa em qualquer parte do conteúdo.

Uso:
    .venv/bin/python scripts/backfill_page_chunks.py [opções]

Opções:
    --min-content-length N   Mínimo de chars para chunkar (default: 2000)
    --chunk-size N           Tokens por chunk (default: 400)
    --chunk-overlap N        Overlap em tokens (default: 50)
    --batch-size N           Textos por chamada à Vertex AI (default: 50)
    --dry-run                Mostra o que seria processado sem persistir nada
    --no-embeddings          Só cria os chunks, sem gerar embeddings
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill_page_chunks")

_MAX_TOKENS_PER_CALL = 15_000
_CHARS_PER_TOKEN = 4


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _vec_to_str(embedding: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in embedding) + "]"


def _split_by_tokens(
    items: list[tuple[int, str]], max_tokens: int = _MAX_TOKENS_PER_CALL
) -> list[list[tuple[int, str]]]:
    batches: list[list[tuple[int, str]]] = []
    current: list[tuple[int, str]] = []
    current_tokens = 0
    for item_id, text in items:
        estimated = max(1, len(text) // _CHARS_PER_TOKEN)
        if current and current_tokens + estimated > max_tokens:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append((item_id, text))
        current_tokens += estimated
    if current:
        batches.append(current)
    return batches


# ─── Etapa 1: criar chunks ────────────────────────────────────────────────────


async def create_page_chunks(
    pool,
    min_content_length: int,
    chunk_size: int,
    chunk_overlap: int,
    dry_run: bool,
) -> int:
    """Chunka main_content de páginas que ainda não têm page_chunks. Retorna total criado."""
    from app.adapters.outbound.document_processor.chunker import TextChunker

    chunker = TextChunker(chunk_size=chunk_size, overlap=chunk_overlap)

    async with pool.acquire() as conn:
        pages = await conn.fetch(
            """
            SELECT p.id, p.url, p.title, p.main_content
            FROM pages p
            WHERE length(p.main_content) >= $1
              AND NOT EXISTS (
                  SELECT 1 FROM page_chunks pc WHERE pc.page_id = p.id
              )
            ORDER BY length(p.main_content) DESC
            """,
            min_content_length,
        )

    logger.info("%d páginas longas sem page_chunks.", len(pages))
    if dry_run:
        for p in pages[:10]:
            logger.info(
                "  [dry-run] %s — %d chars (%s)",
                p["title"], len(p["main_content"]), p["url"],
            )
        return 0

    total_chunks = 0
    for page in pages:
        page_id = page["id"]
        page_url = page["url"]
        content = page["main_content"] or ""

        chunks = chunker.chunk(text=content, document_url=page_url)
        if not chunks:
            continue

        async with pool.acquire() as conn:
            for c in chunks:
                try:
                    await conn.execute(
                        """
                        INSERT INTO page_chunks
                            (page_id, page_url, chunk_index, chunk_text, token_count)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT (page_url, chunk_index) DO NOTHING
                        """,
                        page_id,
                        page_url,
                        c.chunk_index,
                        c.text,
                        c.token_count,
                    )
                    total_chunks += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Erro ao inserir chunk page_id=%d idx=%d: %s",
                        page_id, c.chunk_index, exc,
                    )

        logger.info(
            "  ✓ %s — %d chunks criados (%d chars)",
            page["title"][:60], len(chunks), len(content),
        )

    return total_chunks


# ─── Etapa 2: gerar embeddings ────────────────────────────────────────────────


async def backfill_embeddings(
    pool,
    gateway,
    model_name: str,
    batch_size: int,
    dry_run: bool,
) -> tuple[int, int]:
    """Gera embeddings para page_chunks sem vetor. Retorna (processados, falhas)."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT pc.id, pc.chunk_text
            FROM page_chunks pc
            WHERE NOT EXISTS (
                SELECT 1 FROM embeddings e
                WHERE e.source_type = 'page_chunk'
                  AND e.source_id   = pc.id
                  AND e.model_name  = $1
            )
            ORDER BY pc.id
            """,
            model_name,
        )

    items = [(r["id"], r["chunk_text"]) for r in rows]
    total = len(items)
    logger.info("%d page_chunks sem embedding.", total)

    if dry_run or total == 0:
        return 0, 0

    # Divide em lotes por tokens
    item_batches: list[list[tuple[int, str]]] = []
    for i in range(0, total, batch_size):
        item_batches.extend(_split_by_tokens(items[i : i + batch_size]))

    processed = 0
    failures = 0

    for batch_num, batch in enumerate(item_batches, start=1):
        ids = [b[0] for b in batch]
        texts = [b[1] for b in batch]
        est_tokens = sum(len(t) // _CHARS_PER_TOKEN for t in texts)

        logger.info(
            "Embedding batch %d/%d — %d textos / ~%d tokens (ids %d..%d)",
            batch_num, len(item_batches), len(batch), est_tokens, ids[0], ids[-1],
        )

        try:
            embeddings = await gateway.embed_batch(texts, task_type="RETRIEVAL_DOCUMENT")
        except Exception as exc:  # noqa: BLE001
            logger.error("Batch %d falhou: %s — pulando.", batch_num, exc)
            failures += len(batch)
            continue

        async with pool.acquire() as conn:
            for (item_id, text), emb in zip(batch, embeddings):
                vec_str = _vec_to_str(emb)
                src_hash = _sha256(text)
                try:
                    await conn.execute(
                        """
                        INSERT INTO embeddings
                            (source_type, source_id, source_text_hash,
                             model_name, dimensions, embedding)
                        VALUES ('page_chunk', $1, $2, $3, $4, $5::vector)
                        ON CONFLICT (source_type, source_id, model_name)
                        DO UPDATE SET
                            embedding        = EXCLUDED.embedding,
                            source_text_hash = EXCLUDED.source_text_hash,
                            created_at       = NOW()
                        """,
                        item_id,
                        src_hash,
                        model_name,
                        768,
                        vec_str,
                    )
                    processed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error("Falha ao persistir id=%d: %s", item_id, exc)
                    failures += 1

        logger.info("Batch %d/%d concluído.", batch_num, len(item_batches))

    return processed, failures


# ─── Main ─────────────────────────────────────────────────────────────────────


async def run(
    min_content_length: int,
    chunk_size: int,
    chunk_overlap: int,
    batch_size: int,
    dry_run: bool,
    no_embeddings: bool,
) -> None:
    from app.adapters.outbound.postgres.connection import DatabasePool
    from app.adapters.outbound.vertex_ai.embedding_gateway import VertexEmbeddingGateway
    from app.config.settings import get_settings

    settings = get_settings()
    model_name = settings.vertex_embedding_model

    logger.info(
        "Iniciando backfill de page_chunks "
        "(min_content=%d | chunk_size=%d | overlap=%d | modelo=%s)",
        min_content_length, chunk_size, chunk_overlap, model_name,
    )

    start = time.monotonic()

    db = DatabasePool(settings)
    async with db:
        pool = db._pool  # noqa: SLF001

        # ── Etapa 1: criar chunks ─────────────────────────────────────────────
        logger.info("=== Etapa 1: criando page_chunks ===")
        total_chunks = await create_page_chunks(
            pool, min_content_length, chunk_size, chunk_overlap, dry_run
        )

        # ── Etapa 2: embeddings ───────────────────────────────────────────────
        processed = 0
        failures = 0
        if not no_embeddings:
            logger.info("=== Etapa 2: gerando embeddings para page_chunks ===")
            gateway = VertexEmbeddingGateway(
                project_id=settings.vertex_project_id,
                location=settings.vertex_embedding_location,
                model_name=model_name,
                output_dimensionality=settings.embedding_dimensions,
                max_batch_size=batch_size,
                cache_max_size=0,
            )
            processed, failures = await backfill_embeddings(
                pool, gateway, model_name, batch_size, dry_run
            )

    elapsed = time.monotonic() - start

    print("\n" + "=" * 60)
    print("RELATÓRIO — backfill_page_chunks")
    print("=" * 60)
    print(f"  Modelo:              {model_name}")
    print(f"  Min content length:  {min_content_length} chars")
    print(f"  Chunk size:          {chunk_size} tokens")
    print(f"  Chunk overlap:       {chunk_overlap} tokens")
    print(f"  Dry run:             {dry_run}")
    print(f"  Chunks criados:      {total_chunks}")
    print(f"  Embeddings gerados:  {processed}")
    print(f"  Falhas:              {failures}")
    print(f"  Tempo total:         {elapsed:.1f}s")
    print("=" * 60)

    if failures > 0:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill de page_chunks e embeddings para páginas longas"
    )
    parser.add_argument(
        "--min-content-length", type=int, default=2000,
        help="Mínimo de chars para incluir página (default: 2000)",
    )
    parser.add_argument(
        "--chunk-size", type=int, default=400,
        help="Tokens por chunk (default: 400)",
    )
    parser.add_argument(
        "--chunk-overlap", type=int, default=50,
        help="Overlap em tokens entre chunks consecutivos (default: 50)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=50,
        help="Textos por chamada à Vertex AI (default: 50)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostra o que seria processado sem persistir nada",
    )
    parser.add_argument(
        "--no-embeddings", action="store_true",
        help="Só cria os chunks, sem gerar embeddings",
    )
    args = parser.parse_args()

    asyncio.run(run(
        min_content_length=args.min_content_length,
        chunk_size=args.chunk_size,
        chunk_overlap=args.chunk_overlap,
        batch_size=args.batch_size,
        dry_run=args.dry_run,
        no_embeddings=args.no_embeddings,
    ))


if __name__ == "__main__":
    main()
