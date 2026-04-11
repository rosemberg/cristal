"""Input port: DataHealthCheckUseCase ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.value_objects.data_inconsistency import DataInconsistency, HealthCheckReport


class DataHealthCheckUseCase(ABC):
    """Verifica a saúde dos dados (páginas, documentos, links) e registra inconsistências."""

    @abstractmethod
    async def check_pages(self, concurrency: int = 5) -> HealthCheckReport:
        """Verifica acessibilidade de todas as páginas cadastradas."""
        ...

    @abstractmethod
    async def check_documents(self, concurrency: int = 5) -> HealthCheckReport:
        """Verifica acessibilidade de todos os documentos cadastrados."""
        ...

    @abstractmethod
    async def check_links(self, concurrency: int = 10) -> HealthCheckReport:
        """Verifica validade dos links em page_links."""
        ...

    @abstractmethod
    async def check_all(self, concurrency: int = 5) -> HealthCheckReport:
        """Executa check_pages + check_documents + check_links e agrega os resultados."""
        ...

    @abstractmethod
    async def get_inconsistencies(
        self,
        status: str = "open",
        resource_type: str | None = None,
        severity: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DataInconsistency]:
        """Lista inconsistências filtradas por status, tipo de recurso e severidade."""
        ...

    @abstractmethod
    async def resolve_inconsistency(
        self, inconsistency_id: int, resolution_note: str, resolved_by: str
    ) -> None:
        """Marca uma inconsistência como resolvida."""
        ...

    @abstractmethod
    async def acknowledge_inconsistency(self, inconsistency_id: int) -> None:
        """Marca uma inconsistência como reconhecida (em análise)."""
        ...

    @abstractmethod
    async def ignore_inconsistency(self, inconsistency_id: int, reason: str) -> None:
        """Marca uma inconsistência como ignorada com justificativa."""
        ...
