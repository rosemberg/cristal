"""Unit tests — HttpContentFetcher (Etapa 8).

Testa o adapter HTTP sem acionar a rede real.
httpx.AsyncClient é mockado via unittest.mock.patch + AsyncMock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.adapters.outbound.http.content_fetcher import HttpContentFetcher
from app.domain.ports.outbound.content_fetch_gateway import FetchResult

VALID_URL = "https://www.tre-pi.jus.br/transparencia"
PDF_URL = "https://www.tre-pi.jus.br/relatorio.pdf"
INVALID_URL = "https://www.example.com/pagina"

SIMPLE_HTML = "<html><body><h1>Título</h1><p>Texto principal</p></body></html>"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_mock_response(
    status_code: int = 200,
    text: str = SIMPLE_HTML,
    content_type: str = "text/html; charset=utf-8",
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.text = text
    response.headers = {"content-type": content_type}
    return response


def make_client_context(response: MagicMock) -> MagicMock:
    """Cria o mock de httpx.AsyncClient como context manager."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ─── Restrição de domínio ─────────────────────────────────────────────────────


class TestDomainRestriction:
    async def test_fetch_allowed_domain_succeeds(self) -> None:
        cm = make_client_context(make_mock_response())
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert result.ok is True

    async def test_fetch_disallowed_domain_returns_error(self) -> None:
        fetcher = HttpContentFetcher()
        result = await fetcher.fetch(INVALID_URL)
        assert result.ok is False
        assert result.error is not None
        assert result.content == ""
        assert result.status_code == 0

    async def test_fetch_disallowed_domain_does_not_make_http_call(self) -> None:
        cm = make_client_context(make_mock_response())
        with patch(
            "app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm
        ) as mock_cls:
            fetcher = HttpContentFetcher()
            await fetcher.fetch(INVALID_URL)
        mock_cls.assert_not_called()

    async def test_fetch_subdomain_is_permitted(self) -> None:
        url = "https://sistemas.tre-pi.jus.br/consulta"
        cm = make_client_context(make_mock_response())
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(url)
        assert result.ok is True

    async def test_fetch_custom_allowed_domain(self) -> None:
        url = "https://www.example.com/pagina"
        cm = make_client_context(make_mock_response())
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher(allowed_domain="example.com")
            result = await fetcher.fetch(url)
        assert result.ok is True


# ─── Fetch bem-sucedido ──────────────────────────────────────────────────────


class TestSuccessfulFetch:
    async def test_fetch_returns_fetch_result(self) -> None:
        cm = make_client_context(make_mock_response())
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert isinstance(result, FetchResult)
        assert result.url == VALID_URL

    async def test_fetch_extracts_text_from_html(self) -> None:
        html = "<html><body><h1>Título da Página</h1><p>Texto principal aqui.</p></body></html>"
        cm = make_client_context(make_mock_response(text=html))
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert "Título da Página" in result.content
        assert "Texto principal aqui." in result.content

    async def test_fetch_removes_script_tags(self) -> None:
        html = (
            "<html><body><script>alert('xss')</script>"
            "<p>Conteúdo legítimo</p></body></html>"
        )
        cm = make_client_context(make_mock_response(text=html))
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert "alert" not in result.content
        assert "Conteúdo legítimo" in result.content

    async def test_fetch_removes_style_tags(self) -> None:
        html = (
            "<html><head><style>body { color: red; }</style></head>"
            "<body><p>Texto visível</p></body></html>"
        )
        cm = make_client_context(make_mock_response(text=html))
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert "color: red" not in result.content
        assert "Texto visível" in result.content

    async def test_fetch_status_200_ok_is_true(self) -> None:
        cm = make_client_context(make_mock_response(status_code=200))
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert result.ok is True
        assert result.status_code == 200

    async def test_fetch_error_field_is_none_on_success(self) -> None:
        cm = make_client_context(make_mock_response())
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert result.error is None


# ─── Erros HTTP ──────────────────────────────────────────────────────────────


class TestHttpErrors:
    async def test_fetch_404_returns_status_404(self) -> None:
        cm = make_client_context(make_mock_response(status_code=404, text="Not Found"))
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert result.status_code == 404
        assert result.ok is False

    async def test_fetch_500_returns_status_500(self) -> None:
        cm = make_client_context(make_mock_response(status_code=500, text="Server Error"))
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert result.status_code == 500
        assert result.ok is False

    async def test_fetch_timeout_sets_error(self) -> None:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)

        assert result.ok is False
        assert result.error is not None
        assert result.status_code == 0

    async def test_fetch_connection_error_sets_error(self) -> None:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)

        assert result.ok is False
        assert result.error is not None

    async def test_fetch_error_result_has_correct_url(self) -> None:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)

        assert result.url == VALID_URL


# ─── Detecção de PDF ─────────────────────────────────────────────────────────


class TestPdfDetection:
    async def test_detect_pdf_by_content_type(self) -> None:
        cm = make_client_context(
            make_mock_response(content_type="application/pdf", text="%PDF-1.4")
        )
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert result.is_pdf is True
        assert result.content == ""

    async def test_detect_pdf_by_url_extension(self) -> None:
        cm = make_client_context(
            make_mock_response(content_type="application/octet-stream")
        )
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(PDF_URL)
        assert result.is_pdf is True

    async def test_html_is_not_detected_as_pdf(self) -> None:
        cm = make_client_context(make_mock_response())
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert result.is_pdf is False

    async def test_pdf_result_has_empty_content(self) -> None:
        cm = make_client_context(
            make_mock_response(content_type="application/pdf")
        )
        with patch("app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm):
            fetcher = HttpContentFetcher()
            result = await fetcher.fetch(VALID_URL)
        assert result.content == ""


# ─── Cache ────────────────────────────────────────────────────────────────────


class TestCache:
    async def test_second_fetch_same_url_hits_cache(self) -> None:
        cm = make_client_context(make_mock_response(text="<p>Conteúdo</p>"))
        with patch(
            "app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm
        ) as mock_cls:
            fetcher = HttpContentFetcher()
            r1 = await fetcher.fetch(VALID_URL)
            r2 = await fetcher.fetch(VALID_URL)

        # HTTP client instanciado só uma vez (segundo fetch vem do cache)
        assert mock_cls.call_count == 1
        assert r1 == r2

    async def test_different_urls_are_cached_separately(self) -> None:
        url2 = "https://www.tre-pi.jus.br/outra-pagina"
        cm = make_client_context(make_mock_response())
        with patch(
            "app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm
        ) as mock_cls:
            fetcher = HttpContentFetcher()
            await fetcher.fetch(VALID_URL)
            await fetcher.fetch(url2)

        # Duas URLs distintas → duas chamadas HTTP
        assert mock_cls.call_count == 2

    async def test_non_200_responses_not_cached(self) -> None:
        cm = make_client_context(make_mock_response(status_code=404))
        with patch(
            "app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm
        ) as mock_cls:
            fetcher = HttpContentFetcher()
            await fetcher.fetch(VALID_URL)
            await fetcher.fetch(VALID_URL)

        # 404 não é cacheado → duas chamadas HTTP
        assert mock_cls.call_count == 2

    async def test_error_responses_not_cached(self) -> None:
        client = AsyncMock()
        client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.adapters.outbound.http.content_fetcher.httpx.AsyncClient", return_value=cm
        ) as mock_cls:
            fetcher = HttpContentFetcher()
            await fetcher.fetch(VALID_URL)
            await fetcher.fetch(VALID_URL)

        # Erros de conexão não são cacheados
        assert mock_cls.call_count == 2
