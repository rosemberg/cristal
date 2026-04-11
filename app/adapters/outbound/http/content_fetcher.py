"""HTTP Content Fetcher adapter — HttpContentFetcher.

Implementa ContentFetchGateway usando httpx (async) + BeautifulSoup4.

Regras de negócio embutidas no adapter:
  - Somente domínio tre-pi.jus.br é permitido (configurável via construtor).
  - Timeout de 10 s por requisição.
  - Cache em memória com TTL de 1 h (somente respostas 200 OK são cacheadas).
  - Detecção de PDF por Content-Type ou extensão de URL.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.domain.ports.outbound.content_fetch_gateway import (
    ContentFetchGateway,
    FetchResult,
)

logger = logging.getLogger(__name__)

_DEFAULT_ALLOWED_DOMAIN = "tre-pi.jus.br"
_DEFAULT_TIMEOUT = 10.0
_DEFAULT_CACHE_TTL = 3600  # 1 hora


class HttpContentFetcher(ContentFetchGateway):
    """Adapter HTTP que busca e extrai texto de páginas do portal TRE-PI."""

    def __init__(
        self,
        allowed_domain: str = _DEFAULT_ALLOWED_DOMAIN,
        timeout: float = _DEFAULT_TIMEOUT,
        cache_ttl: int = _DEFAULT_CACHE_TTL,
    ) -> None:
        self._allowed_domain = allowed_domain
        self._timeout = timeout
        self._cache_ttl = cache_ttl
        self._cache: dict[str, FetchResult] = {}
        self._cache_timestamps: dict[str, float] = {}

    async def fetch(self, url: str) -> FetchResult:
        """Busca URL e retorna texto extraído ou metadados de PDF."""
        # ── Restrição de domínio ──────────────────────────────────────────────
        parsed = urlparse(url)
        if not parsed.netloc.endswith(self._allowed_domain):
            logger.warning("Fetch bloqueado — domínio não permitido: %s", parsed.netloc)
            return FetchResult(
                url=url,
                content="",
                status_code=0,
                error=f"Domínio não permitido: {parsed.netloc}",
            )

        # ── Cache ─────────────────────────────────────────────────────────────
        if url in self._cache:
            age = time.monotonic() - self._cache_timestamps[url]
            if age < self._cache_ttl:
                logger.debug("Cache hit para %s (idade: %.0fs)", url, age)
                return self._cache[url]

        # ── HTTP ──────────────────────────────────────────────────────────────
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            ) as client:
                response = await client.get(url)

            content_type = response.headers.get("content-type", "")
            is_pdf = "application/pdf" in content_type or url.lower().endswith(".pdf")

            if is_pdf:
                result = FetchResult(
                    url=url,
                    content="",
                    status_code=response.status_code,
                    is_pdf=True,
                )
            else:
                extracted = self._extract_text(response.text)
                result = FetchResult(
                    url=url,
                    content=extracted,
                    status_code=response.status_code,
                )

            if response.status_code == 200:
                self._cache[url] = result
                self._cache_timestamps[url] = time.monotonic()

            return result

        except httpx.TimeoutException as exc:
            logger.warning("Timeout ao buscar %s: %s", url, exc)
            return FetchResult(url=url, content="", status_code=0, error=f"Timeout: {exc}")
        except httpx.RequestError as exc:
            logger.warning("Erro de conexão ao buscar %s: %s", url, exc)
            return FetchResult(
                url=url, content="", status_code=0, error=f"Erro de conexão: {exc}"
            )

    @staticmethod
    def _extract_text(html: str) -> str:
        """Extrai texto limpo de HTML, removendo scripts, estilos e navegação."""
        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        return soup.get_text(separator="\n", strip=True)
