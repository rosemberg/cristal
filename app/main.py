import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import ALLOWED_ORIGINS, KNOWLEDGE_BASE_PATH
from app.routers.chat import router, limiter
from app.services.knowledge_base import load_knowledge_base
from app.services.content_fetcher import ContentFetcher
from app.services.vertex_client import VertexClient
from app.services.chat_engine import ChatEngine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Cristal — Transparência Chat")

    kb = load_knowledge_base(KNOWLEDGE_BASE_PATH)
    fetcher = ContentFetcher()
    vertex = VertexClient()
    engine = ChatEngine(kb=kb, fetcher=fetcher, vertex=vertex)

    app.state.chat_engine = engine
    logger.info("Application ready")

    yield

    logger.info("Shutting down")


app = FastAPI(
    title="Cristal — Transparência Chat",
    description="Assistente de IA para o portal de Transparência do TRE-PI",
    version="1.0.0",
    lifespan=lifespan,
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(router)

# Serve static frontend
import os
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(static_dir, "index.html"))
