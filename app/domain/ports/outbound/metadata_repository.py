"""Output port: MetadataRepository ABC.

Abstração para persistência e consulta de entidades e tags de páginas
nas tabelas `page_entities` e `page_tags` (migration 009).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.value_objects.enriched_metadata import PageEntity, PageTag


class MetadataRepository(ABC):
    """Port para CRUD de entidades e tags de páginas."""

    @abstractmethod
    async def save_entities_batch(self, entities: list[PageEntity]) -> list[int]:
        """Persiste um lote de entidades. Retorna lista de IDs gerados."""

    @abstractmethod
    async def save_tags_batch(self, tags: list[PageTag]) -> None:
        """Persiste um lote de tags (ON CONFLICT DO NOTHING para UNIQUE)."""

    @abstractmethod
    async def get_covered_page_ids(self) -> set[int]:
        """Retorna IDs de páginas que já têm pelo menos uma tag (Etapa B completa)."""

    @abstractmethod
    async def delete_by_page(self, page_id: int) -> None:
        """Remove todas as entidades e tags de uma página (para reenrichment)."""

    @abstractmethod
    async def delete_entities_by_page(self, page_id: int) -> None:
        """Remove apenas as entidades de uma página (sem apagar tags)."""

    @abstractmethod
    async def get_status(self) -> dict[str, object]:
        """Retorna estatísticas: total de entidades por tipo e tags por nome."""
