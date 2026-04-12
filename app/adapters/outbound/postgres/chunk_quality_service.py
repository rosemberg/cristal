"""PostgresChunkQualityService: wires ChunkQualityScorer with the DB pool."""

from __future__ import annotations

import asyncpg

from app.adapters.outbound.postgres.chunk_quality_repo import PostgresChunkQualityRepository
from app.domain.services.chunk_quality_scorer import ChunkQualityScorer


class PostgresChunkQualityService(ChunkQualityScorer):
    """Concrete scorer backed by a PostgreSQL pool."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        super().__init__(PostgresChunkQualityRepository(pool))
