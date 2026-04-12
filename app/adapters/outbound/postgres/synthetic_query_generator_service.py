"""PostgreSQL-backed SyntheticQueryGeneratorService.

Estende o serviço de domínio com acesso direto ao pool asyncpg para
buscar chunks pendentes sem cobertura de perguntas sintéticas.

Separa a lógica de geração (domain) do acesso a dados (adapter).
"""

from __future__ import annotations

import logging

import asyncpg

from app.domain.ports.outbound.embedding_gateway import EmbeddingGateway
from app.domain.ports.outbound.embedding_repository import EmbeddingRepository
from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.ports.outbound.synthetic_query_repository import SyntheticQueryRepository
from app.domain.services.synthetic_query_generator import SyntheticQueryGeneratorService

logger = logging.getLogger(__name__)

# Mapa: source_type → tabela e coluna de texto
_SOURCE_TABLE_MAP = {
    "page_chunk": ("page_chunks", "chunk_text"),
    "chunk": ("document_chunks", "content"),
}


class PostgresSyntheticQueryGeneratorService(SyntheticQueryGeneratorService):
    """Implementação concreta com acesso ao banco via asyncpg."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        llm_gateway: LLMGateway,
        embedding_gateway: EmbeddingGateway,
        synthetic_query_repo: SyntheticQueryRepository,
        embedding_repo: EmbeddingRepository,
        model_name: str = "gemini-2.5-flash-lite",
        llm_batch_size: int = 10,
    ) -> None:
        super().__init__(
            llm_gateway=llm_gateway,
            embedding_gateway=embedding_gateway,
            synthetic_query_repo=synthetic_query_repo,
            embedding_repo=embedding_repo,
            model_name=model_name,
            llm_batch_size=llm_batch_size,
        )
        self._pool = pool

    async def _fetch_pending_from_db(
        self, source_type: str, covered: set[int], limit: int
    ) -> list[dict]:
        """Busca chunks sem cobertura no banco, com LIMIT."""
        mapping = _SOURCE_TABLE_MAP.get(source_type)
        if mapping is None:
            logger.warning("SQGenerator: source_type desconhecido '%s'", source_type)
            return []

        table, text_col = mapping
        covered_list = list(covered) if covered else [-1]

        # Cria placeholder $2, $3, ... para a lista de IDs cobertos
        placeholders = ", ".join(f"${i+2}" for i in range(len(covered_list)))

        query = f"""
            SELECT id, {text_col} AS text
            FROM {table}
            WHERE id NOT IN ({placeholders})
              AND {text_col} IS NOT NULL
              AND LENGTH({text_col}) > 50
            ORDER BY id
            LIMIT $1
        """  # noqa: S608 — tabela e coluna são constantes do código, não user input

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, limit, *covered_list)

        return [{"id": row["id"], "text": row["text"]} for row in rows]

    async def _fetch_single_chunk(
        self, source_type: str, source_id: int
    ) -> dict | None:
        """Busca um único chunk pelo ID."""
        mapping = _SOURCE_TABLE_MAP.get(source_type)
        if mapping is None:
            return None

        table, text_col = mapping

        query = f"SELECT id, {text_col} AS text FROM {table} WHERE id = $1"  # noqa: S608

        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, source_id)

        if row is None:
            return None
        return {"id": row["id"], "text": row["text"]}
