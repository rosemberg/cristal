"""Output port: SyntheticQueryRepository ABC.

Abstração para persistência e consulta de perguntas sintéticas na tabela
`synthetic_queries`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.value_objects.synthetic_query import SyntheticQuery


class SyntheticQueryRepository(ABC):
    """Port para CRUD de perguntas sintéticas."""

    @abstractmethod
    async def save_batch(self, queries: list[SyntheticQuery]) -> list[int]:
        """Persiste um lote de perguntas. Retorna lista de IDs gerados."""

    @abstractmethod
    async def get_covered_source_ids(self, source_type: str) -> set[int]:
        """Retorna conjunto de source_ids que já têm perguntas sintéticas."""

    @abstractmethod
    async def delete_by_source(self, source_type: str, source_id: int) -> None:
        """Remove todas as perguntas de uma fonte (para regeneração)."""

    @abstractmethod
    async def count_by_source_type(self, source_type: str) -> int:
        """Retorna total de perguntas para o source_type dado."""

    @abstractmethod
    async def get_status(self) -> dict[str, int]:
        """Retorna contagens por source_type, ex: {'page_chunk': 3000, 'chunk': 500}."""
