"""FastAPI app factory — Cristal 2.0.

A função create_app() aceita um lifespan opcional para facilitar testes
sem banco real. Em produção, o lifespan padrão inicializa o pool asyncpg
e wires todos os serviços em app.state.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.adapters.inbound.fastapi.analytics_router import router as analytics_router
from app.adapters.inbound.fastapi.chat_router import router as chat_router
from app.adapters.inbound.fastapi.document_router import router as document_router
from app.adapters.inbound.fastapi.health_router import router as health_router
from app.adapters.inbound.fastapi.ingestion_router import router as ingestion_router
from app.adapters.inbound.fastapi.session_router import router as session_router

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan padrão (produção)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _default_lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    """Inicializa serviços e injeta em app.state."""
    logger.info("Iniciando Cristal 2.0")

    from app.adapters.outbound.postgres.analytics_repo import PostgresAnalyticsRepository
    from app.adapters.outbound.postgres.connection import DatabasePool
    from app.adapters.outbound.postgres.document_repo import PostgresDocumentRepository
    from app.adapters.outbound.postgres.search_repo import PostgresSearchRepository
    from app.adapters.outbound.postgres.session_repo import PostgresSessionRepository
    from app.adapters.outbound.vertex_ai.gateway import VertexAIGateway
    from app.config.settings import get_settings
    from app.domain.services.chat_service import ChatService
    from app.domain.services.document_service import DocumentService
    from app.domain.services.session_service import SessionService

    settings = get_settings()
    app.state.settings = settings

    db = DatabasePool(settings)
    await db.__aenter__()
    app.state.db_pool = db

    from app.adapters.outbound.postgres.connection import get_pool
    pool = get_pool(db)

    search_repo = PostgresSearchRepository(pool)
    document_repo = PostgresDocumentRepository(pool)
    session_repo = PostgresSessionRepository(pool)
    analytics_repo = PostgresAnalyticsRepository(pool)
    llm = VertexAIGateway(
        project_id=settings.vertex_project_id,
        location=settings.vertex_location,
        model_name=settings.vertex_model,
    )

    # Verificar se schema existe (fail-fast antes de servir requests)
    async with pool.acquire() as conn:
        tables = await conn.fetchval(
            "SELECT COUNT(*) FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name='pages'"
        )
        if tables == 0:
            logger.error("Schema não encontrado. Execute: alembic upgrade head")
            raise RuntimeError("Database schema not initialized")

    app.state.search_repo = search_repo
    app.state.analytics_repo = analytics_repo

    app.state.chat_service = ChatService(
        search_repo=search_repo,
        session_repo=session_repo,
        analytics_repo=analytics_repo,
        llm=llm,
    )
    app.state.document_service = DocumentService(document_repo=document_repo)
    app.state.session_service = SessionService(session_repo=session_repo)

    # Serviços de ingestão e health check [V2]
    from app.adapters.outbound.postgres.document_ingestion_repo import (
        PostgresDocumentIngestionRepository,
    )
    from app.adapters.outbound.postgres.inconsistency_repo import PostgresInconsistencyRepository
    from app.domain.services.data_health_check_service import DataHealthCheckService
    from app.domain.services.document_ingestion_service import DocumentIngestionService

    doc_ingestion_repo = PostgresDocumentIngestionRepository(pool)
    inconsistency_repo = PostgresInconsistencyRepository(pool)

    try:
        from app.adapters.outbound.http.document_download_gateway import (
            HttpDocumentDownloadGateway,
        )
        download_gw = HttpDocumentDownloadGateway()
    except ImportError:
        from app.adapters.outbound.http.document_downloader import HttpDocumentDownloader
        download_gw = HttpDocumentDownloader()  # type: ignore[assignment]

    try:
        from app.adapters.outbound.process.document_process_gateway import (
            DefaultDocumentProcessGateway,
        )
        process_gw = DefaultDocumentProcessGateway()
    except ImportError:
        from app.adapters.outbound.process.document_process_adapter import (
            DocumentProcessAdapter,
        )
        process_gw = DocumentProcessAdapter()  # type: ignore[assignment]

    app.state.ingestion_service = DocumentIngestionService(
        document_repository=doc_ingestion_repo,
        download_gateway=download_gw,
        process_gateway=process_gw,
        inconsistency_repository=inconsistency_repo,
    )
    app.state.health_check_service = DataHealthCheckService(
        page_repository=PostgresPageRepository(pool),
        document_repository=doc_ingestion_repo,
        download_gateway=download_gw,
        inconsistency_repository=inconsistency_repo,
    )

    logger.info("Aplicação pronta")
    yield

    logger.info("Encerrando Cristal 2.0")
    await db.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(*, lifespan: Callable[..., Any] | None = None) -> FastAPI:
    """Cria a instância FastAPI configurada.

    Args:
        lifespan: Contexto de ciclo de vida assíncrono. Se None, usa o
            lifespan padrão que conecta ao PostgreSQL e inicializa serviços.
            Passe um noop_lifespan em testes para evitar conexão ao banco.
    """
    from app.config.settings import Settings

    # Settings pode não existir sem CRISTAL_VERTEX_PROJECT_ID em testes —
    # usamos defaults tolerantes ao criar o app antes do lifespan.
    try:
        from app.config.settings import get_settings
        settings = get_settings()
        allowed_origins = settings.allowed_origins
    except Exception:  # noqa: BLE001
        allowed_origins = ["*"]

    app = FastAPI(
        title="Cristal — Transparência Chat",
        description="Assistente de IA para o portal de Transparência do TRE-PI",
        version="2.0.0",
        lifespan=lifespan or _default_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["*"],
    )

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(document_router)
    app.include_router(session_router)
    app.include_router(analytics_router)
    app.include_router(ingestion_router)

    # Frontend estático (se existir)
    static_dir = Path(__file__).parents[4] / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(str(static_dir / "index.html"))

    return app
