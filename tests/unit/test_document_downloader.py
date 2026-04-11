"""Unit tests — HttpDocumentDownloader (Etapa 2).

Testa o adapter de download sem acionar a rede real.
httpx.AsyncClient é mockado via unittest.mock.patch + AsyncMock.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.adapters.outbound.http.document_downloader import (
    DocumentDownloadError,
    DomainNotAllowedError,
    HttpDocumentDownloader,
)
from app.domain.ports.outbound.document_download_gateway import (
    DownloadResult,
)

VALID_URL = "https://www.tre-pi.jus.br/doc/relatorio.pdf"
VALID_CSV_URL = "https://sistemas.tre-pi.jus.br/dados/planilha.csv"
INVALID_URL = "https://www.evil.com/malware.pdf"

SMALL_PDF_BYTES = b"%PDF-1.4 fake content"
_1MB = 1024 * 1024


# ─── Helpers ──────────────────────────────────────────────────────────────────


def make_bytes_response(
    status_code: int = 200,
    content: bytes = SMALL_PDF_BYTES,
    content_type: str = "application/pdf",
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.content = content
    response.headers = {"content-type": content_type}
    return response


def make_client_context(response: MagicMock) -> MagicMock:
    """Cria o mock de httpx.AsyncClient como context manager para GET."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ─── Restrição de domínio ─────────────────────────────────────────────────────


class TestDomainRestriction:
    async def test_download_allowed_domain_succeeds(self) -> None:
        cm = make_client_context(make_bytes_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.download(VALID_URL)
        assert isinstance(result, DownloadResult)
        assert result.status_code == 200

    async def test_download_subdomain_is_permitted(self) -> None:
        cm = make_client_context(make_bytes_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.download(VALID_CSV_URL)
        assert isinstance(result, DownloadResult)

    async def test_download_disallowed_domain_raises(self) -> None:
        downloader = HttpDocumentDownloader()
        with pytest.raises(DomainNotAllowedError):
            await downloader.download(INVALID_URL)

    async def test_download_disallowed_domain_does_not_call_http(self) -> None:
        cm = make_client_context(make_bytes_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ) as mock_cls:
            downloader = HttpDocumentDownloader()
            with pytest.raises(DomainNotAllowedError):
                await downloader.download(INVALID_URL)
        mock_cls.assert_not_called()

    async def test_download_custom_allowed_domain(self) -> None:
        url = "https://www.example.com/doc.pdf"
        cm = make_client_context(make_bytes_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader(allowed_domain="example.com")
            result = await downloader.download(url)
        assert isinstance(result, DownloadResult)


# ─── Download bem-sucedido ────────────────────────────────────────────────────


class TestSuccessfulDownload:
    async def test_download_returns_download_result(self) -> None:
        cm = make_client_context(make_bytes_response())
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.download(VALID_URL)
        assert isinstance(result, DownloadResult)

    async def test_download_result_contains_content_bytes(self) -> None:
        cm = make_client_context(make_bytes_response(content=SMALL_PDF_BYTES))
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.download(VALID_URL)
        assert result.content == SMALL_PDF_BYTES

    async def test_download_result_size_bytes_is_len_of_content(self) -> None:
        cm = make_client_context(make_bytes_response(content=SMALL_PDF_BYTES))
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.download(VALID_URL)
        assert result.size_bytes == len(SMALL_PDF_BYTES)

    async def test_download_result_has_content_type(self) -> None:
        cm = make_client_context(
            make_bytes_response(content_type="application/pdf")
        )
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.download(VALID_URL)
        assert "application/pdf" in result.content_type

    async def test_download_csv_content_type(self) -> None:
        cm = make_client_context(
            make_bytes_response(content=b"col1,col2\n1,2", content_type="text/csv")
        )
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader()
            result = await downloader.download(VALID_CSV_URL)
        assert result.status_code == 200


# ─── Limite de tamanho ────────────────────────────────────────────────────────


class TestSizeLimit:
    async def test_download_rejects_document_above_limit(self) -> None:
        large_content = b"x" * (51 * _1MB)  # 51 MB
        cm = make_client_context(make_bytes_response(content=large_content))
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader(max_size_mb=50)
            with pytest.raises(DocumentDownloadError, match="size limit"):
                await downloader.download(VALID_URL)

    async def test_download_accepts_document_at_exact_limit(self) -> None:
        exact_content = b"x" * (50 * _1MB)  # exactly 50 MB
        cm = make_client_context(make_bytes_response(content=exact_content))
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader(max_size_mb=50)
            result = await downloader.download(VALID_URL)
        assert result.size_bytes == 50 * _1MB

    async def test_download_rejects_document_above_custom_limit(self) -> None:
        content = b"x" * (6 * _1MB)  # 6 MB
        cm = make_client_context(make_bytes_response(content=content))
        with patch(
            "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
            return_value=cm,
        ):
            downloader = HttpDocumentDownloader(max_size_mb=5)
            with pytest.raises(DocumentDownloadError):
                await downloader.download(VALID_URL)


# ─── Retry e timeout ─────────────────────────────────────────────────────────


class TestRetryAndTimeout:
    async def test_download_retries_on_timeout(self) -> None:
        """Primeira chamada: timeout. Segunda: sucesso."""
        success_response = make_bytes_response()

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                httpx.TimeoutException("timed out"),
                success_response,
            ]
        )
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
                return_value=cm,
            ),
            patch("app.adapters.outbound.http.document_downloader.asyncio.sleep"),
        ):
            downloader = HttpDocumentDownloader(max_retries=2)
            result = await downloader.download(VALID_URL)
        assert isinstance(result, DownloadResult)

    async def test_download_raises_after_all_retries_exhausted(self) -> None:
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
                return_value=cm,
            ),
            patch("app.adapters.outbound.http.document_downloader.asyncio.sleep"),
        ):
            downloader = HttpDocumentDownloader(max_retries=2)
            with pytest.raises(DocumentDownloadError):
                await downloader.download(VALID_URL)

    async def test_download_retries_on_connection_error(self) -> None:
        success_response = make_bytes_response()

        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=[
                httpx.ConnectError("connection refused"),
                success_response,
            ]
        )
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
                return_value=cm,
            ),
            patch("app.adapters.outbound.http.document_downloader.asyncio.sleep"),
        ):
            downloader = HttpDocumentDownloader(max_retries=2)
            result = await downloader.download(VALID_URL)
        assert isinstance(result, DownloadResult)

    async def test_download_uses_exponential_backoff(self) -> None:
        """Verifica que sleep é chamado com 1s e 2s nos retries."""
        client = AsyncMock()
        client.get = AsyncMock(
            side_effect=httpx.TimeoutException("timed out")
        )
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(return_value=client)
        cm.__aexit__ = AsyncMock(return_value=False)

        with (
            patch(
                "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
                return_value=cm,
            ),
            patch(
                "app.adapters.outbound.http.document_downloader.asyncio.sleep"
            ) as mock_sleep,
        ):
            downloader = HttpDocumentDownloader(max_retries=2)
            with pytest.raises(DocumentDownloadError):
                await downloader.download(VALID_URL)

        sleep_calls = [call.args[0] for call in mock_sleep.call_args_list]
        assert sleep_calls == [1, 2]

    async def test_download_no_sleep_on_first_attempt(self) -> None:
        """Primeira tentativa não faz sleep."""
        cm = make_client_context(make_bytes_response())
        with (
            patch(
                "app.adapters.outbound.http.document_downloader.httpx.AsyncClient",
                return_value=cm,
            ),
            patch(
                "app.adapters.outbound.http.document_downloader.asyncio.sleep"
            ) as mock_sleep,
        ):
            downloader = HttpDocumentDownloader()
            await downloader.download(VALID_URL)
        mock_sleep.assert_not_called()
