"""Output port: EmbeddingRepository ABC.

Abstração para persistência e consulta de embeddings na tabela `embeddings`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class EmbeddingRecord:
    """Representa um embedding armazenado na tabela `embeddings`."""

    source_type: str          # 'chunk' | 'page' | 'table'
    source_id: int            # FK lógica para a entidade correspondente
    embedding: list[float]    # vetor de 768 dims (text-embedding-005)
    source_text_hash: str     # SHA-256 do texto original
    model_name: str = "text-embedding-005"
    dimensions: int = 768


class EmbeddingRepository(ABC):
    """Port para persistência e consulta de embeddings vetoriais."""

    @abstractmethod
    async def save_batch(self, records: list[EmbeddingRecord]) -> None:
        """Persiste um lote de embeddings via INSERT ... ON CONFLICT DO UPDATE.

        Em re-ingestão, sobrescreve o embedding e atualiza o hash.
        """

    @abstractmethod
    async def find_by_source(
        self, source_id: int, source_type: str, model_name: str = "text-embedding-005"
    ) -> EmbeddingRecord | None:
        """Retorna o embedding de uma fonte específica, ou None se não existir."""

    @abstractmethod
    async def delete_by_source(self, source_id: int, source_type: str) -> None:
        """Remove todos os embeddings de uma fonte (para re-indexação completa)."""

    @abstractmethod
    async def get_existing_hashes(
        self, source_type: str, model_name: str = "text-embedding-005"
    ) -> dict[int, str]:
        """Retorna mapa {source_id: source_text_hash} para o source_type dado.

        Usado na ingestão para pular chunks cujo hash não mudou.
        """
