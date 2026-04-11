"""Fixtures compartilhadas para testes E2E (Etapa 9 Pipeline V2).

Reutiliza o PostgreSQL via testcontainers do conftest de integração.
Fornece:
- pool_e2e: pool asyncpg limpo por módulo (sem truncar entre testes)
- Fakes: FakeDownloadGateway, SpyLLMGateway
"""

from __future__ import annotations

import json
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

import docker
import pytest
from testcontainers.postgres import PostgresContainer

from app.config.settings import Settings
from app.domain.ports.outbound.document_download_gateway import (
    AccessCheckResult,
    DocumentDownloadGateway,
    DownloadError,
    DownloadResult,
)
from app.domain.ports.outbound.llm_gateway import LLMGateway

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ─── Docker / testcontainers ──────────────────────────────────────────────────


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001
        return False


docker_required = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon não está disponível",
)


@pytest.fixture(scope="session")
def postgres_container():  # type: ignore[no-untyped-def]
    """Sobe PostgreSQL real via Docker para testes E2E."""
    if not _docker_available():
        pytest.skip("Docker daemon não está disponível.")
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def pg_settings(postgres_container: PostgresContainer) -> Settings:
    """Settings apontando para o PostgreSQL do testcontainer E2E."""
    url = postgres_container.get_connection_url()
    asyncpg_url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    return Settings(
        vertex_project_id="test-project",
        database_url=asyncpg_url,
        db_pool_min=1,
        db_pool_max=5,
        _env_file=None,  # type: ignore[call-arg]
    )


# ─── Alembic ─────────────────────────────────────────────────────────────────


def make_alembic_config(database_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ─── Pool compartilhado para E2E ──────────────────────────────────────────────


@pytest.fixture(scope="module")
def run_migrations_e2e(pg_settings):  # type: ignore[misc]
    cfg = make_alembic_config(pg_settings.database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture
async def pool_e2e(pg_settings, run_migrations_e2e):  # type: ignore[misc]
    """Pool asyncpg limpo (TRUNCATE) para cada teste E2E."""
    p = await asyncpg.create_pool(
        dsn=_asyncpg_dsn(pg_settings.database_url),
        min_size=1,
        max_size=5,
    )
    async with p.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE TABLE data_inconsistencies, query_logs, chat_messages, chat_sessions,
                document_tables, document_chunks, document_contents,
                page_links, navigation_tree, documents, pages
            RESTART IDENTITY CASCADE
            """
        )
    yield p
    await p.close()


# ─── FakeDownloadGateway ──────────────────────────────────────────────────────


class FakeDownloadGateway(DocumentDownloadGateway):
    """Gateway de download configurável: controla quais URLs retornam 200 ou 404.

    Por padrão, todas as URLs são acessíveis (200) e retornam bytes fictícios.
    Configure ``broken_urls`` para simular recursos indisponíveis.
    """

    DEFAULT_CONTENT = b"%PDF-1.4 fake content for testing"
    DEFAULT_CONTENT_TYPE = "application/pdf"

    def __init__(
        self,
        broken_urls: set[str] | None = None,
        custom_content: bytes | None = None,
    ) -> None:
        self._broken = broken_urls or set()
        self._content = custom_content or self.DEFAULT_CONTENT

    def set_broken(self, url: str) -> None:
        self._broken.add(url)

    def set_accessible(self, url: str) -> None:
        self._broken.discard(url)

    async def download(self, url: str) -> DownloadResult:
        if url in self._broken:
            err = DownloadError(f"HTTP 404: {url}")
            err.is_size_limit = False
            raise err
        return DownloadResult(
            content=self._content,
            content_type=self.DEFAULT_CONTENT_TYPE,
            size_bytes=len(self._content),
            status_code=200,
        )

    async def check_accessible(self, url: str) -> AccessCheckResult:
        if url in self._broken:
            return AccessCheckResult(
                url=url,
                accessible=False,
                status_code=404,
                content_type=None,
                content_length=None,
                error="Not Found",
                response_time_ms=10.0,
            )
        return AccessCheckResult(
            url=url,
            accessible=True,
            status_code=200,
            content_type="text/html",
            content_length=1024,
            error=None,
            response_time_ms=10.0,
        )


# ─── SpyLLMGateway ────────────────────────────────────────────────────────────


class SpyLLMGateway(LLMGateway):
    """LLM que registra o que recebeu e retorna uma resposta configurável."""

    def __init__(self, response: dict | None = None) -> None:
        self._response = response or {
            "text": "Resposta E2E de teste.",
            "sources": [],
            "tables": [],
            "suggestions": ["Ver mais", "Detalhes"],
        }
        self.last_system_prompt: str = ""
        self.last_messages: list[dict] = []
        self.call_count: int = 0

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
        temperature: float = 0.3,
    ) -> str:
        self.last_system_prompt = system_prompt
        self.last_messages = list(messages)
        self.call_count += 1
        return json.dumps(self._response, ensure_ascii=False)

    async def generate_stream(self, system_prompt, messages):  # type: ignore[no-untyped-def]
        yield json.dumps(self._response, ensure_ascii=False)
