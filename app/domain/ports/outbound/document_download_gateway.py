"""Output port: DocumentDownloadGateway ABC."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


class DownloadError(Exception):
    """Base domain exception raised by DocumentDownloadGateway implementations.

    Attributes:
        is_size_limit: True if the download was rejected because the document
            exceeds the configured size limit.
    """

    is_size_limit: bool = False


@dataclass(frozen=True)
class DownloadResult:
    """Result of a successful document download."""

    content: bytes
    content_type: str
    size_bytes: int
    status_code: int


@dataclass(frozen=True)
class AccessCheckResult:
    """Result of an HTTP HEAD accessibility check."""

    url: str
    accessible: bool
    status_code: int
    content_type: str | None
    content_length: int | None
    error: str | None
    response_time_ms: float


class DocumentDownloadGateway(ABC):
    """Port: downloads document bytes from a remote URL."""

    @abstractmethod
    async def download(self, url: str) -> DownloadResult:
        """Download document bytes from *url*.

        Args:
            url: The document URL (must be within the allowed domain).

        Returns:
            DownloadResult with raw bytes, content-type, size and HTTP status.

        Raises:
            ValueError: If *url* is outside the allowed domain or exceeds the
                size limit.
            httpx.TimeoutException / httpx.RequestError: Propagated after all
                retry attempts are exhausted.
        """
        ...

    @abstractmethod
    async def check_accessible(self, url: str) -> AccessCheckResult:
        """Perform a HEAD request to check whether *url* is reachable.

        No retry is performed — this is a lightweight probe.

        Args:
            url: The URL to probe (must be within the allowed domain).

        Returns:
            AccessCheckResult with reachability flag, status code and timing.

        Raises:
            ValueError: If *url* is outside the allowed domain.
        """
        ...
