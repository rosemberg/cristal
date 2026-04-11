"""Output port: SearchRepository ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.entities.document_table import DocumentTable
from app.domain.value_objects.search_result import ChunkMatch, PageMatch, SemanticMatch


class SearchRepository(ABC):
    @abstractmethod
    async def search_pages(self, query: str, top_k: int = 5) -> list[PageMatch]: ...

    @abstractmethod
    async def search_chunks(self, query: str, top_k: int = 5) -> list[ChunkMatch]: ...

    @abstractmethod
    async def search_tables(self, query: str) -> list[DocumentTable]: ...

    @abstractmethod
    async def search_semantic(
        self,
        query_embedding: list[float],
        source_type: str = "chunk",
        top_k: int = 5,
        filters: dict[str, object] | None = None,
    ) -> list[SemanticMatch]:
        """Busca semântica via cosine similarity em embeddings pgvector.

        Args:
            query_embedding: Vetor da query (gerado por EmbeddingGateway).
            source_type: Tipo da fonte — 'chunk' | 'page' | 'table'.
            top_k: Máximo de resultados.
            filters: Filtros opcionais, ex: {'category': 'licitações'}.

        Returns:
            Lista de SemanticMatch ordenada por similaridade decrescente.
        """
        ...

    @abstractmethod
    async def get_categories(self) -> list[dict[str, object]]: ...

    @abstractmethod
    async def get_stats(self) -> dict[str, object]: ...
