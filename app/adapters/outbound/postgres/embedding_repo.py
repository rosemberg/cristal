"""PostgreSQL adapter: EmbeddingRepository.

Persiste e consulta embeddings na tabela `embeddings` (criada na migration 004).
Usa string-format para o tipo vector do pgvector: '[x,y,z]'::vector.
"""

from __future__ import annotations

import logging

import asyncpg

from app.domain.ports.outbound.embedding_repository import EmbeddingRecord, EmbeddingRepository

logger = logging.getLogger(__name__)


def _vec_to_str(embedding: list[float]) -> str:
    """Converte list[float] para string pgvector: '[x,y,z]'."""
    return "[" + ",".join(repr(x) for x in embedding) + "]"


class PostgresEmbeddingRepository(EmbeddingRepository):
    """Implementa EmbeddingRepository via asyncpg + pgvector."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def save_batch(self, records: list[EmbeddingRecord]) -> None:
        """Persiste embeddings via UPSERT (INSERT ON CONFLICT DO UPDATE)."""
        if not records:
            return

        async with self._pool.acquire() as conn:
            for rec in records:
                vec_str = _vec_to_str(rec.embedding)
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
                    rec.source_type,
                    rec.source_id,
                    rec.source_text_hash,
                    rec.model_name,
                    rec.dimensions,
                    vec_str,
                )

        logger.debug(
            "EmbeddingRepository: %d embeddings persistidos (tipo=%s)",
            len(records),
            records[0].source_type if records else "?",
        )

    async def find_by_source(
        self,
        source_id: int,
        source_type: str,
        model_name: str = "text-embedding-005",
    ) -> EmbeddingRecord | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT source_type, source_id, source_text_hash, model_name,
                       dimensions, embedding::text
                FROM embeddings
                WHERE source_type = $1
                  AND source_id   = $2
                  AND model_name  = $3
                LIMIT 1
                """,
                source_type,
                source_id,
                model_name,
            )
        if row is None:
            return None
        return EmbeddingRecord(
            source_type=row["source_type"],
            source_id=row["source_id"],
            source_text_hash=row["source_text_hash"] or "",
            model_name=row["model_name"],
            dimensions=row["dimensions"],
            embedding=_parse_vec(row["embedding"]),
        )

    async def delete_by_source(self, source_id: int, source_type: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM embeddings WHERE source_type = $1 AND source_id = $2",
                source_type,
                source_id,
            )

    async def get_existing_hashes(
        self,
        source_type: str,
        model_name: str = "text-embedding-005",
    ) -> dict[int, str]:
        """Retorna {source_id: source_text_hash} para o source_type dado."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT source_id, source_text_hash
                FROM embeddings
                WHERE source_type = $1
                  AND model_name  = $2
                """,
                source_type,
                model_name,
            )
        return {row["source_id"]: (row["source_text_hash"] or "") for row in rows}


def _parse_vec(s: str | None) -> list[float]:
    """Converte string pgvector '[x,y,z]' → list[float]."""
    if not s:
        return []
    return [float(x) for x in s.strip("[]").split(",")]
