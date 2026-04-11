"""Unit tests — HttpDocumentDownloader.check_accessible (Etapa 2).

Testa o HEAD request de verificação de acessibilidade sem rede real.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.adapters.outbound.http.document_downloader import (
    DomainNotAllowedError,
    HttpDocumentDownloader,
)
from app.domain.ports.outbound.document_download_gateway import AccessCheckResult

VALID_URL = "https://www.tre-pi.jus.br/transparencia/doc.pdf"
INVALID_URL = "https://www.outsider.com/file.pdf"


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_head_response(
    status_code: int = 200,
    content_type: str | None = "application/pdf",
    content_length: str | None = "12345",
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    headers: dict[str, str] = {}
    if content_type:
        headers["content-type"] = content_type
    if content_length:
        headers["content-length"] = content_length
    response.headers = headers
    return response


def make_head_client_context(response: MagicMock) -> MagicMock:
    client = AsyncMock()
    client.head = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ─── Domínio ─────────────────────────────────────────────────────────────────


class TestCheckAccessibleDomain:
    async def test_check_allowed_domain_does_not_raise(self) -> None:
        cm = make_head_client_context(make_head_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert isinstance(result, AccessCheckResult)

    async def test_check_disallowed_domain_raises(self) -> None:
        downloader = HttpDocumentDownloader()
        with pytest.raises(DomainNotAllowedError):
            await downloader.check_accessible(INVALID_URL)

    async def test_check_disallowed_domain_does_not_call_http(self) -> None:
        cm = make_head_client_context(make_head_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ) as mock_cls:
            downloader = HttpDocumentDownloader()
            with pytest.raises(DomainNotAllowedError):
                await downloader.check_accessible(INVALID_URL)
        mock_cls.assert_not_called()


# ─── Resultados de acessibilidade ─────────────────────────────────────────────


class TestCheckAccessibleResults:
    async def test_status_200_is_accessible(self) -> None:
        cm = make_head_client_context(make_head_response(status_code=200))
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.accessible is True
        assert result.status_code == 200

    async def test_status_404_is_not_accessible(self) -> None:
        cm = make_head_client_context(make_head_response(status_code=404))
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.accessible is False
        assert result.status_code == 404

    async def test_status_500_is_not_accessible(self) -> None:
        cm = make_head_client_context(make_head_response(status_code=500))
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.accessible is False

    async def test_status_301_redirect_is_accessible(self) -> None:
        """3xx com follow_redirects=True deve resultar em 200 final — simulamos 301 como exemplo."""
        cm = make_head_client_context(make_head_response(status_code=301))
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.accessible is True

    async def test_result_contains_url(self) -> None:
        cm = make_head_client_context(make_head_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.url == VALID_URL

    async def test_result_contains_content_type(self) -> None:
        cm = make_head_client_context(
            make_head_response(content_type="application/pdf")
        )
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.content_type == "application/pdf"

    async def test_result_contains_content_length(self) -> None:
        cm = make_head_client_context(
            make_head_response(content_length="98765")
        )
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.content_length == 98765

    async def test_result_content_length_none_when_header_absent(self) -> None:
        cm = make_head_client_context(
            make_head_response(content_length=None)
        )
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.content_length is None

    async def test_result_error_is_none_on_success(self) -> None:
        cm = make_head_client_context(make_head_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.error is None

    async def test_result_response_time_ms_is_positive(self) -> None:
        cm = make_head_client_context(make_head_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)
        assert result.response_time_ms >= 0


# ─── Erros de rede ────────────────────────────────────────────────────────────


class TestCheckAccessibleErrors:
    async def test_timeout_returns_inaccessible(self) -> None:
        client = AsyncMock()
        client.head = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)

        assert result.accessible is False
        assert result.status_code == 0
        assert result.error is not None
        assert "Timeout" in result.error

    async def test_connection_error_returns_inaccessible(self) -> None:
        client = AsyncMock()
        client.head = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.check_accessible(VALID_URL)

        assert result.accessible is False
        assert result.error is not None

    async def test_check_does_not_retry_on_error(self) -> None:
        """check_accessible não tem retry — falha na primeira tentativa."""
        client = AsyncMock()
        client.head = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ) as mock_cls:
            downloader = HttpDocumentDownloader(max_retries=2)
            await downloader.check_accessible(VALID_URL)

        # Uma única instanciação do client (sem retry)
        assert mock_cls.call_count == 1
