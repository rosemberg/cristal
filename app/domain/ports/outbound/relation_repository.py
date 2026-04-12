"""Port: RelationRepository (Fase 6 NOVO_RAG)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.value_objects.document_relation import DocumentRelation, RelationExtractionResult


class RelationRepository(ABC):
    """Persistência do grafo de relações entre páginas."""

    @abstractmethod
    async def save_relations_batch(self, relations: list[DocumentRelation]) -> int:
        """Insere relações ignorando conflitos (ON CONFLICT DO NOTHING).

        Retorna o número de linhas efetivamente inseridas.
        """

    @abstractmethod
    async def get_related_pages(
        self,
        page_id: int,
        relation_types: list[str] | None = None,
        limit: int = 10,
    ) -> list[DocumentRelation]:
        """Retorna relações onde source_page_id = page_id (ou target = page_id)."""

    @abstractmethod
    async def get_covered_page_ids(self, strategy: str | None = None) -> set[int]:
        """IDs de páginas que já têm relações extraídas para a strategy informada.

        Se strategy=None retorna todas as páginas com pelo menos uma relação.
        """

    @abstractmethod
    async def get_status(self) -> dict[str, int]:
        """Totais por strategy e por relation_type."""
