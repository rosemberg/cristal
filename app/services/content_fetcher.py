import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from cachetools import TTLCache

from app.config import CACHE_TTL_SECONDS, MAX_CONTENT_LENGTH

if TYPE_CHECKING:
    from app.services.knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

ALLOWED_DOMAIN = "tre-pi.jus.br"
REQUEST_TIMEOUT = 10.0


@dataclass
class FetchResult:
    url: str
    content: Optional[str]
    content_type: str   # "html", "pdf", "csv", "video", "api", "error"
    is_media: bool
    truncated: bool = False


class ContentFetcher:
    def __init__(self) -> None:
        self._cache: TTLCache = TTLCache(maxsize=256, ttl=CACHE_TTL_SECONDS)

    def _is_allowed(self, url: str) -> bool:
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        return host.endswith(ALLOWED_DOMAIN)

    def _detect_type_from_url(self, url: str) -> str:
        url_lower = url.lower()
        if url_lower.endswith(".pdf"):
            return "pdf"
        if url_lower.endswith(".csv"):
            return "csv"
        if "youtube" in url_lower or "transmissao-ao-vivo" in url_lower:
            return "video"
        if "swagger" in url_lower or url_lower.rstrip("/").endswith("/api"):
            return "api"
        return "html"

    def _detect_type_from_headers(self, content_type_header: str) -> str:
        ct = content_type_header.lower()
        if "pdf" in ct:
            return "pdf"
        if "csv" in ct or "spreadsheet" in ct or "excel" in ct:
            return "csv"
        if "video" in ct:
            return "video"
        return "html"

    def _extract_main_content(self, html: str) -> str:
        soup = BeautifulSoup(html, "lxml")

        # Remove unwanted tags
        for tag in soup(["script", "style", "nav", "footer", "header",
                         "aside", "noscript", "form", "iframe"]):
            tag.decompose()

        # Try to find main content area
        main = (
            soup.find("main")
            or soup.find(id="content")
            or soup.find(id="main-content")
            or soup.find(class_="content")
            or soup.find(class_="main-content")
            or soup.find("article")
            or soup.find("section")
            or soup.body
        )

        if main is None:
            return soup.get_text(separator=" ", strip=True)

        text = main.get_text(separator=" ", strip=True)
        # Collapse multiple spaces/newlines
        import re
        text = re.sub(r"\s{2,}", " ", text)
        return text.strip()

    async def fetch_page_content(self, url: str) -> FetchResult:
        if not self._is_allowed(url):
            logger.warning("URL not in allowed domain: %s", url)
            return FetchResult(url=url, content=None, content_type="error", is_media=False)

        # Check cache
        if url in self._cache:
            logger.debug("Cache hit for %s", url)
            return self._cache[url]

        # Quick type detection from URL
        url_type = self._detect_type_from_url(url)
        if url_type in ("pdf", "csv", "video", "api"):
            result = FetchResult(url=url, content=None, content_type=url_type, is_media=True)
            self._cache[url] = result
            return result

        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": "TREPITransparenciaBot/1.0"},
            ) as client:
                response = await client.get(url)
                response.raise_for_status()

                # Detect type from Content-Type header
                ct_header = response.headers.get("content-type", "")
                detected_type = self._detect_type_from_headers(ct_header)

                if detected_type in ("pdf", "csv", "video"):
                    result = FetchResult(url=url, content=None, content_type=detected_type, is_media=True)
                    self._cache[url] = result
                    return result

                # Extract text from HTML
                raw_text = self._extract_main_content(response.text)
                truncated = False

                if len(raw_text) > MAX_CONTENT_LENGTH:
                    raw_text = raw_text[:MAX_CONTENT_LENGTH]
                    truncated = True

                result = FetchResult(
                    url=url,
                    content=raw_text,
                    content_type="html",
                    is_media=False,
                    truncated=truncated,
                )
                self._cache[url] = result
                return result

        except httpx.TimeoutException:
            logger.warning("Timeout fetching %s", url)
            return FetchResult(url=url, content=None, content_type="error", is_media=False)
        except httpx.HTTPStatusError as exc:
            logger.warning("HTTP %s fetching %s", exc.response.status_code, url)
            return FetchResult(url=url, content=None, content_type="error", is_media=False)
        except Exception as exc:
            logger.error("Error fetching %s: %s", url, exc)
            return FetchResult(url=url, content=None, content_type="error", is_media=False)

    async def get_from_db_or_fetch(
        self, url: str, kb: "KnowledgeBase | None" = None
    ) -> FetchResult:
        """
        Verifica o banco de conhecimento antes de fazer requisição HTTP.
        Se main_content existir no banco, retorna sem fazer request ao site.
        Só aciona o scraping HTTP quando o conteúdo não está disponível localmente.
        """
        if kb is not None:
            page = kb.get_page_with_context(url)
            if page and page.get("main_content"):
                content = page["main_content"]
                truncated = len(content) > MAX_CONTENT_LENGTH
                if truncated:
                    content = content[:MAX_CONTENT_LENGTH]
                logger.debug("Conteúdo servido do banco local para %s", url)
                return FetchResult(
                    url=url,
                    content=content,
                    content_type="html",
                    is_media=False,
                    truncated=truncated,
                )
        return await self.fetch_page_content(url)
