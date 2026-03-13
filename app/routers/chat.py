import logging
from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models.schemas import (
    CategoriesResponse,
    ChatRequest,
    ChatResponse,
    HealthResponse,
    SuggestResponse,
)
from app.config import RATE_LIMIT_PER_MINUTE

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api")


def get_chat_engine(request: Request):
    return request.app.state.chat_engine


def get_knowledge_base(request: Request):
    return request.app.state.knowledge_base


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(f"{RATE_LIMIT_PER_MINUTE}/minute")
async def chat(
    request: Request,
    body: ChatRequest,
    engine=Depends(get_chat_engine),
) -> ChatResponse:
    logger.info("Chat request: %.100s", body.message)
    response = await engine.process_message(body.message, body.history)
    return response


@router.get("/suggest", response_model=SuggestResponse)
async def suggest(request: Request, engine=Depends(get_chat_engine)) -> SuggestResponse:
    """Retorna sugestões de perguntas baseadas nas categorias da base de conhecimento."""
    return SuggestResponse(suggestions=engine.get_initial_suggestions())


@router.get("/categories", response_model=CategoriesResponse)
async def categories(
    request: Request, kb=Depends(get_knowledge_base)
) -> CategoriesResponse:
    """Retorna a lista de categorias disponíveis na base de conhecimento com contagem de páginas."""
    return CategoriesResponse(categories=kb.get_categories())


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Retorna status da aplicação e estatísticas da base de conhecimento."""
    try:
        kb = request.app.state.knowledge_base
        stats = kb.get_stats()
    except Exception:
        stats = {}
    return HealthResponse(status="ok", knowledge_stats=stats)
