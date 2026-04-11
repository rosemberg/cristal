"""Output port: PageRepository ABC.

Abstração para persistir páginas crawleadas com seus documentos,
links internos e entradas de navegação.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class CrawledDocument:
    """Documento (PDF/CSV/XLSX) vinculado a uma página crawleada."""

    document_url: str
    document_title: str = ""
    document_type: str = "pdf"
    context: str = ""


@dataclass
class CrawledLink:
    """Link interno encontrado em uma página crawleada."""

    target_url: str
    link_title: str = ""
    link_type: str = "internal"


@dataclass
class CrawledPage:
    """Value object produzido pelo crawler para uma única página."""

    url: str
    title: str
    description: str = ""
    main_content: str = ""
    content_summary: str = ""
    category: str = ""
    subcategory: str = ""
    content_type: str = "page"
    depth: int = 0
    parent_url: str = ""
    breadcrumb: list[dict[str, object]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    documents: list[CrawledDocument] = field(default_factory=list)
    internal_links: list[CrawledLink] = field(default_factory=list)
    last_modified: datetime | None = None


class PageRepository(ABC):
    """Port para persistir e consultar páginas crawleadas."""

    @abstractmethod
    async def upsert_page(self, data: CrawledPage) -> None:
        """Insere ou atualiza uma página e seus documentos/links associados.

        Idempotente: re-executar com a mesma URL não duplica dados.
        """
        ...

    @abstractmethod
    async def count_pages(self) -> int:
        """Retorna o total de páginas no banco."""
        ...
