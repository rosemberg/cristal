"""Input port: DocumentIngestionUseCase ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from app.domain.value_objects.ingestion import IngestionStats, IngestionStatus

# Callback de progresso: (current, total, item_url, is_error)
ProgressCallback = Callable[[int, int, str, bool], None] | None


class DocumentIngestionUseCase(ABC):
    """Orquestra o pipeline de ingestão de documentos (download → processamento → persistência)."""

    @abstractmethod
    async def ingest_pending(
        self,
        concurrency: int = 3,
        on_progress: ProgressCallback = None,
    ) -> IngestionStats:
        """Processa todos os documentos com status 'pending'."""
        ...

    @abstractmethod
    async def ingest_single(self, document_url: str) -> bool:
        """Processa um único documento por URL. Retorna True se bem-sucedido."""
        ...

    @abstractmethod
    async def reprocess_errors(self, on_progress: ProgressCallback = None) -> IngestionStats:
        """Reprocessa documentos que estão com status 'error'."""
        ...

    @abstractmethod
    async def resume(
        self,
        concurrency: int = 3,
        on_progress: ProgressCallback = None,
    ) -> IngestionStats:
        """Retoma pipeline após crash: reseta docs stuck em 'processing' e processa pendentes."""
        ...

    @abstractmethod
    async def get_status(self) -> IngestionStatus:
        """Retorna snapshot dos contadores de status do pipeline."""
        ...
