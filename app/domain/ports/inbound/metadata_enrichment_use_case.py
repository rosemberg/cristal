"""Input port: MetadataEnrichmentUseCase Protocol.

Define a interface para enriquecimento de metadados estruturados (Fase 3 NOVO_RAG).
"""

from __future__ import annotations

from typing import Protocol

from app.domain.value_objects.enriched_metadata import EnrichmentResult


class MetadataEnrichmentUseCase(Protocol):
    """Use case para extração e gestão de metadados estruturados de páginas."""

    async def enrich_pending_pages(
        self,
        batch_size: int = 50,
    ) -> EnrichmentResult:
        """Executa Etapa A (regex) + Etapa B (LLM) para páginas sem tags.

        Args:
            batch_size: Número de páginas processadas por chamada ao LLM.
        """
        ...

    async def enrich_all_pages_regex(
        self,
        batch_size: int = 200,
    ) -> EnrichmentResult:
        """Executa apenas Etapa A (regex) em todas as páginas, sem LLM.

        Útil para reprocessar rapidamente após ajustes nos padrões regex.
        """
        ...

    async def enrich_pending_pages_llm(
        self,
        batch_size: int = 50,
    ) -> EnrichmentResult:
        """Executa apenas Etapa B (LLM) para páginas sem tags.

        Não executa regex — adiciona/atualiza apenas tags e entidades LLM.
        """
        ...

    async def reenrich_page(self, page_id: int) -> tuple[int, int]:
        """Remove e regenera entidades e tags para uma página específica.

        Retorna (qtd_entidades, qtd_tags) geradas.
        """
        ...

    async def get_status(self) -> dict[str, object]:
        """Retorna status atual do enriquecimento: contagens e pendentes."""
        ...
