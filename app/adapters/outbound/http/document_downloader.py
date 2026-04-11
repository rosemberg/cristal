"""HTTP Document Downloader adapter — HttpDocumentDownloader.

Implementa DocumentDownloadGateway usando httpx (async).

Regras de negócio:
  - Somente domínio tre-pi.jus.br é permitido (configurável via construtor).
  - Timeout de 30s por download; 10s para verificação de acessibilidade.
  - Retry: até 2 tentativas com backoff exponencial (1s, 2s).
  - Limite de tamanho: 50MB (configurável via settings).
  - User-Agent institucional.
  - check_accessible: HEAD request sem retry (sonda leve).
"""

from __future__ import annotations

import asyncio
import logging
import time
from urllib.parse import urlparse

import httpx

from app.domain.ports.outbound.document_download_gateway import (
    AccessCheckResult,
    DocumentDownloadGateway,
    DownloadResult,
)

logger = logging.getLogger(__name__)

_DEFAULT_ALLOWED_DOMAIN = "tre-pi.jus.br"
_DEFAULT_TIMEOUT = 30.0
_DEFAULT_CHECK_TIMEOUT = 10.0
_DEFAULT_MAX_RETRIES = 2
_DEFAULT_MAX_SIZE_MB = 50
_USER_AGENT = (
    "Mozilla/5.0 (compatible; Cristal/2.0; +https://transparencia.tre-pi.jus.br)"
)


class DocumentDownloadError(Exception):
    """Raised when a document cannot be downloaded after all retries."""


class DomainNotAllowedError(DocumentDownloadError):
    """Raised when the request URL is outside the allowed domain."""


class HttpDocumentDownloader(DocumentDownloadGateway):
    """Downloads documents from the TRE-PI portal with retry and size limit."""

    def __init__(
        self,
        timeout: float = _DEFAULT_TIMEOUT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        max_size_mb: int = _DEFAULT_MAX_SIZE_MB,
        allowed_domain: str = _DEFAULT_ALLOWED_DOMAIN,
    ) -> None:
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._allowed_domain = allowed_domain

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _validate_domain(self, url: str) -> None:
        """Raise DomainNotAllowedError if *url* is outside the allowed domain."""
        parsed = urlparse(url)
        if not parsed.netloc.endswith(self._allowed_domain):
            raise DomainNotAllowedError(
                f"Domínio não permitido: {parsed.netloc!r}. "
                f"Apenas URLs de {self._allowed_domain!r} são aceitas."
            )

    # ── Public interface ─────────────────────────────────────────────────────

    async def download(self, url: str) -> DownloadResult:
        """Download *url* with retry on transient errors.

        Raises:
            ValueError: Domain not allowed or document exceeds size limit.
            httpx.TimeoutException: After all retry attempts exhausted.
            httpx.RequestError: After all retry attempts exhausted.
        """
        self._validate_domain(url)

        last_exc: Exception | None = None
        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = 2 ** (attempt - 1)  # 1s, 2s
                logger.debug(
                    "Aguardando %ds antes da tentativa %d: %s", delay, attempt + 1, url
                )
                await asyncio.sleep(delay)

            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout,
                    follow_redirects=True,
                    headers={"User-Agent": _USER_AGENT},
                ) as client:
                    response = await client.get(url)

                content = response.content
                size_bytes = len(content)

                if size_bytes > self._max_size_bytes:
                    max_mb = self._max_size_bytes // (1024 * 1024)
                    raise DocumentDownloadError(
                        f"Document exceeds size limit: "
                        f"{size_bytes / 1024 / 1024:.1f}MB > {max_mb}MB: {url}"
                    )

                return DownloadResult(
                    content=content,
                    content_type=response.headers.get("content-type", ""),
                    size_bytes=size_bytes,
                    status_code=response.status_code,
                )

            except DocumentDownloadError:
                raise  # size limit — não faz sentido retry

            except httpx.TimeoutException as exc:
                logger.warning(
                    "Timeout no download (tentativa %d/%d): %s",
                    attempt + 1,
                    self._max_retries + 1,
                    url,
                )
                last_exc = exc

            except httpx.RequestError as exc:
                logger.warning(
                    "Erro de conexão (tentativa %d/%d): %s — %s",
                    attempt + 1,
                    self._max_retries + 1,
                    url,
                    exc,
                )
                last_exc = exc

        assert last_exc is not None  # always set if we reach here
        raise DocumentDownloadError(
            f"Download failed after {self._max_retries + 1} attempt(s): {last_exc}"
        ) from last_exc

    async def check_accessible(self, url: str) -> AccessCheckResult:
        """Probe *url* with a HEAD request (no retry).

        Returns AccessCheckResult regardless of outcome; never raises on HTTP
        errors or network issues.

        Raises:
            DomainNotAllowedError: Domain not allowed.
        """
        self._validate_domain(url)

        start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=_DEFAULT_CHECK_TIMEOUT,
                follow_redirects=True,
                headers={"User-Agent": _USER_AGENT},
            ) as client:
                response = await client.head(url)

            elapsed_ms = (time.monotonic() - start) * 1000

            content_length_str = response.headers.get("content-length")
            content_length = int(content_length_str) if content_length_str else None

            return AccessCheckResult(
                url=url,
                accessible=200 <= response.status_code < 400,
                status_code=response.status_code,
                content_type=response.headers.get("content-type"),
                content_length=content_length,
                error=None,
                response_time_ms=elapsed_ms,
            )

        except httpx.TimeoutException as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.debug("Timeout na verificação de acessibilidade: %s", url)
            return AccessCheckResult(
                url=url,
                accessible=False,
                status_code=0,
                content_type=None,
                content_length=None,
                error=f"Timeout: {exc}",
                response_time_ms=elapsed_ms,
            )

        except httpx.RequestError as exc:
            elapsed_ms = (time.monotonic() - start) * 1000
            logger.debug("Erro de conexão na verificação de acessibilidade: %s — %s", url, exc)
            return AccessCheckResult(
                url=url,
                accessible=False,
                status_code=0,
                content_type=None,
                content_length=None,
                error=f"Erro de conexão: {exc}",
                response_time_ms=elapsed_ms,
            )
