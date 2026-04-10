"""Output port: ContentFetchGateway ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class FetchResult:
    url: str
    content: str  # extracted text (empty string if fetch failed)
    status_code: int
    is_pdf: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status_code == 200 and self.error is None


class ContentFetchGateway(ABC):
    @abstractmethod
    async def fetch(self, url: str) -> FetchResult: ...
