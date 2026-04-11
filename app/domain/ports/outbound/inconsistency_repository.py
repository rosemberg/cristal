"""Output port: InconsistencyRepository ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.value_objects.data_inconsistency import DataInconsistency
from app.domain.value_objects.inconsistency_summary import InconsistencySummary


class InconsistencyRepository(ABC):
    @abstractmethod
    async def save(self, inconsistency: DataInconsistency) -> int:
        """Persiste uma nova inconsistência. Retorna o ID gerado."""

    @abstractmethod
    async def upsert(
        self,
        resource_url: str,
        inconsistency_type: str,
        inconsistency: DataInconsistency,
    ) -> int:
        """Atualiza registro aberto existente (mesmo URL + tipo) ou cria um novo.

        Incrementa retry_count e atualiza last_checked_at em re-detecções.
        Retorna o ID do registro afetado.
        """

    @abstractmethod
    async def list_by_status(
        self,
        status: str = "open",
        resource_type: str | None = None,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DataInconsistency]:
        """Lista inconsistências filtradas por status, tipo de recurso e severidade."""

    @abstractmethod
    async def count_by_status(self) -> dict[str, int]:
        """Retorna contagem agrupada por status: {open: N, acknowledged: N, ...}"""

    @abstractmethod
    async def count_by_type(self, status: str = "open") -> dict[str, int]:
        """Retorna contagem agrupada por inconsistency_type para o status dado."""

    @abstractmethod
    async def update_status(
        self,
        inconsistency_id: int,
        status: str,
        resolved_by: str | None = None,
        resolution_note: str | None = None,
    ) -> None:
        """Altera o status de uma inconsistência pelo ID."""

    @abstractmethod
    async def mark_resolved_by_url(
        self,
        resource_url: str,
        inconsistency_type: str,
        resolution_note: str,
    ) -> int:
        """Resolve todas as inconsistências abertas de um recurso+tipo. Retorna qtd."""

    @abstractmethod
    async def get_summary(self) -> InconsistencySummary:
        """Retorna resumo agregado para o dashboard admin."""
