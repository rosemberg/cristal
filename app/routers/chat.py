import logging
from fastapi import APIRouter, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address

from app.models.schemas import ChatRequest, ChatResponse, HealthResponse, SuggestResponse
from app.config import RATE_LIMIT_PER_MINUTE

logger = logging.getLogger(__name__)
limiter = Limiter(key_func=get_remote_address)

router = APIRouter(prefix="/api")


def get_chat_engine(request: Request):
    return request.app.state.chat_engine


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
    return SuggestResponse(suggestions=engine.get_initial_suggestions())


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")
