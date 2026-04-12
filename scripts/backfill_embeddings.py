#!/usr/bin/env python3
"""Script: backfill_embeddings.py

Preenche a tabela `embeddings` para chunks, tabelas e pages que ainda não
possuem vetor semântico. Útil após a migração de dados existentes ou após
ativar o EmbeddingGateway pela primeira vez.

Uso:
    .venv/bin/python scripts/backfill_embeddings.py [--batch-size 100] [--dry-run]

Opções:
    --batch-size N   Textos por chamada à Vertex AI (default: 100)
    --source-types   Tipos a processar: chunk,page,table (default: chunk,page,table)
    --dry-run        Mostra o que seria processado sem persistir nada
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import sys
import time
from pathlib import Path

# Permite rodar com `python scripts/backfill_embeddings.py` a partir da raiz
sys.path.insert(0, str(Path(__file__).parents[1]))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("backfill")


# ─── Helpers ──────────────────────────────────────────────────────────────────

# text-embedding-005: limite de 20.000 tokens por chamada.
# Margem segura: 15.000. Estimativa: 1 token ≈ 4 caracteres.
_MAX_TOKENS_PER_CALL = 15_000
_CHARS_PER_TOKEN = 4


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_by_tokens(
    items: list[tuple[int, str]], max_tokens: int = _MAX_TOKENS_PER_CALL
) -> list[list[tuple[int, str]]]:
    """Divide itens em sub-lotes respeitando o limite de tokens estimados."""
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


def _vec_to_str(embedding: list[float]) -> str:
    return "[" + ",".join(repr(x) for x in embedding) + "]"


# ─── Fetchers de textos sem embedding ─────────────────────────────────────────


async def _fetch_chunks_without_embedding(
    conn, model_name: str
) -> list[tuple[int, str]]:
    """Retorna (chunk_id, chunk_text) para chunks sem embedding no modelo dado."""
    rows = await conn.fetch(
        """
        SELECT dc.id, dc.chunk_text
        FROM document_chunks dc
        WHERE dc.chunk_text IS NOT NULL
          AND dc.chunk_text <> ''
          AND NOT EXISTS (
              SELECT 1 FROM embeddings e
              WHERE e.source_type = 'chunk'
                AND e.source_id   = dc.id
                AND e.model_name  = $1
          )
        ORDER BY dc.id
        """,
        model_name,
    )
    return [(r["id"], r["chunk_text"]) for r in rows]


async def _fetch_pages_without_embedding(
    conn, model_name: str
) -> list[tuple[int, str]]:
    """Retorna (page_id, text) para pages sem embedding (usa content_summary ou description)."""
    rows = await conn.fetch(
        """
        SELECT p.id,
               COALESCE(p.content_summary, p.description, p.title) AS embed_text
        FROM pages p
        WHERE COALESCE(p.content_summary, p.description, p.title) IS NOT NULL
          AND COALESCE(p.content_summary, p.description, p.title) <> ''
          AND NOT EXISTS (
              SELECT 1 FROM embeddings e
              WHERE e.source_type = 'page'
                AND e.source_id   = p.id
                AND e.model_name  = $1
          )
        ORDER BY p.id
        """,
        model_name,
    )
    return [(r["id"], r["embed_text"]) for r in rows]


async def _fetch_tables_without_embedding(
    conn, model_name: str
) -> list[tuple[int, str]]:
    """Retorna (table_id, text) para tabelas sem embedding."""
    rows = await conn.fetch(
        """
        SELECT dt.id,
               dt.caption,
               dt.headers::text AS headers_text
        FROM document_tables dt
        WHERE NOT EXISTS (
            SELECT 1 FROM embeddings e
            WHERE e.source_type = 'table'
              AND e.source_id   = dt.id
              AND e.model_name  = $1
        )
        ORDER BY dt.id
        """,
        model_name,
    )
    result: list[tuple[int, str]] = []
    for r in rows:
        parts = [r["caption"] or "", r["headers_text"] or ""]
        text = " ".join(p for p in parts if p).strip()
        if text:
            result.append((r["id"], text))
    return result


# ─── Processamento em batch ───────────────────────────────────────────────────


async def _process_source_type(
    pool,
    gateway,
    source_type: str,
    items: list[tuple[int, str]],
    batch_size: int,
    model_name: str,
    dry_run: bool,
) -> tuple[int, int]:
    """Processa todos os itens de um source_type. Retorna (processados, falhas).

    O batching é feito por tokens estimados (não por quantidade de itens)
    para respeitar o limite de 20.000 tokens/chamada do text-embedding-005.
    O parâmetro batch_size é usado como limite de itens por lote antes da
    divisão por tokens (duplo controle: itens + tokens).
    """
    processed = 0
    failures = 0
    total = len(items)

    logger.info("[%s] %d item(s) sem embedding.", source_type, total)
    if dry_run:
        logger.info("[%s] --dry-run: nenhuma persistência.", source_type)
        return 0, 0

    # Divide primeiro por batch_size (itens), depois cada sub-lote por tokens
    item_batches: list[list[tuple[int, str]]] = []
    for i in range(0, total, batch_size):
        item_chunk = items[i : i + batch_size]
        item_batches.extend(_split_by_tokens(item_chunk))

    total_batches = len(item_batches)

    for batch_num, batch in enumerate(item_batches, start=1):
        ids = [item[0] for item in batch]
        texts = [item[1] for item in batch]
        est_tokens = sum(len(t) // _CHARS_PER_TOKEN for t in texts)

        logger.info(
            "[%s] Batch %d/%d — %d textos / ~%d tokens estimados (ids %d..%d)",
            source_type, batch_num, total_batches,
            len(batch), est_tokens, ids[0], ids[-1],
        )

        try:
            embeddings = await gateway.embed_batch(texts, task_type="RETRIEVAL_DOCUMENT")
        except Exception as exc:  # noqa: BLE001
            logger.error("[%s] Batch %d falhou: %s — pulando.", source_type, batch_num, exc)
            failures += len(batch)
            continue

        # Persiste
        async with pool.acquire() as conn:
            for (item_id, text), embedding in zip(batch, embeddings):
                vec_str = _vec_to_str(embedding)
                src_hash = _sha256(text)
                try:
                    await conn.execute(
                        """
                        INSERT INTO embeddings
                            (source_type, source_id, source_text_hash,
                             model_name, dimensions, embedding)
                        VALUES ($1, $2, $3, $4, $5, $6::vector)
                        ON CONFLICT (source_type, source_id, model_name)
                        DO UPDATE SET
                            embedding        = EXCLUDED.embedding,
                            source_text_hash = EXCLUDED.source_text_hash,
                            created_at       = NOW()
                        """,
                        source_type,
                        item_id,
                        src_hash,
                        model_name,
                        768,
                        vec_str,
                    )
                    processed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "[%s] Falha ao persistir id=%d: %s", source_type, item_id, exc
                    )
                    failures += 1

        logger.info("[%s] Batch %d/%d concluído.", source_type, batch_num, total_batches)

    return processed, failures


# ─── Main ─────────────────────────────────────────────────────────────────────


async def run(batch_size: int, source_types: list[str], dry_run: bool) -> None:
    from app.adapters.outbound.postgres.connection import DatabasePool
    from app.adapters.outbound.vertex_ai.embedding_gateway import VertexEmbeddingGateway
    from app.config.settings import get_settings

    settings = get_settings()
    model_name = settings.vertex_embedding_model

    logger.info("Iniciando backfill de embeddings (modelo=%s)", model_name)
    logger.info("source_types=%s | batch_size=%d | dry_run=%s", source_types, batch_size, dry_run)

    # Inicializa gateway
    gateway = VertexEmbeddingGateway(
        project_id=settings.vertex_project_id,
        location=settings.vertex_embedding_location,
        model_name=model_name,
        output_dimensionality=settings.embedding_dimensions,
        max_batch_size=batch_size,
        cache_max_size=0,  # sem cache em backfill — todos são documentos novos
    )

    start = time.monotonic()
    total_processed = 0
    total_failures = 0

    db = DatabasePool(settings)
    async with db:
        pool = db._pool  # noqa: SLF001 — acesso interno necessário

        for stype in source_types:
            async with pool.acquire() as conn:
                if stype == "chunk":
                    items = await _fetch_chunks_without_embedding(conn, model_name)
                elif stype == "page":
                    items = await _fetch_pages_without_embedding(conn, model_name)
                elif stype == "table":
                    items = await _fetch_tables_without_embedding(conn, model_name)
                else:
                    logger.warning("Tipo desconhecido ignorado: %s", stype)
                    continue

            processed, failures = await _process_source_type(
                pool, gateway, stype, items, batch_size, model_name, dry_run
            )
            total_processed += processed
            total_failures += failures

    elapsed = time.monotonic() - start

    # ── Relatório final ────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("RELATÓRIO DE BACKFILL DE EMBEDDINGS")
    print("=" * 60)
    print(f"  Modelo:            {model_name}")
    print(f"  Source types:      {', '.join(source_types)}")
    print(f"  Batch size:        {batch_size}")
    print(f"  Dry run:           {dry_run}")
    print(f"  Total processado:  {total_processed}")
    print(f"  Falhas:            {total_failures}")
    print(f"  Tempo total:       {elapsed:.1f}s")
    print("=" * 60)

    if total_failures > 0:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill de embeddings para RAG V2")
    parser.add_argument(
        "--batch-size", type=int, default=100,
        help="Número de textos por chamada à Vertex AI (default: 100)",
    )
    parser.add_argument(
        "--source-types", type=str, default="chunk,page,table",
        help="Tipos a processar, separados por vírgula (default: chunk,page,table)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Mostra o que seria processado sem persistir nada",
    )
    args = parser.parse_args()
    source_types = [s.strip() for s in args.source_types.split(",") if s.strip()]

    asyncio.run(run(args.batch_size, source_types, args.dry_run))


if __name__ == "__main__":
    main()
