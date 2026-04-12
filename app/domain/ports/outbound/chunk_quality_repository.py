"""Port: ChunkQualityRepository (Fase 5 NOVO_RAG)."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.value_objects.chunk_quality import ChunkQualityResult, QualityReport


class ChunkQualityRepository(ABC):
    """Persistência de scores de qualidade e quarentena de chunks."""

    @abstractmethod
    async def save_quality_batch(self, results: list[ChunkQualityResult]) -> None:
        """Persiste scores e flags; marca quarantined quando score < 0.5."""

    @abstractmethod
    async def fetch_unscored_chunks(
        self,
        table: str,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict]:
        """Retorna chunks sem quality_score calculado (IS NULL).

        Cada dict contém: id, chunk_text (e section_title, page_number para document_chunks).
        """

    @abstractmethod
    async def count_unscored(self, table: str) -> int:
        """Total de chunks sem quality_score no table informado."""

    @abstractmethod
    async def fetch_all_texts(
        self,
        table: str,
        limit: int = 5000,
        offset: int = 0,
    ) -> list[dict]:
        """Retorna id + chunk_text para deduplicação (sem filtro de score)."""

    @abstractmethod
    async def mark_duplicates_quarantined(
        self,
        table: str,
        duplicate_ids: list[int],
    ) -> int:
        """Marca os ids como quarantined=true e adiciona flag 'duplicate'."""

    @abstractmethod
    async def get_report(self) -> QualityReport:
        """Agrega estatísticas de qualidade de ambas as tabelas."""
