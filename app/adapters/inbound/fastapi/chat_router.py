"""Router: Chat — POST /api/chat, GET /api/suggest."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.adapters.inbound.fastapi.dependencies import get_chat_use_case
from app.adapters.inbound.fastapi.schemas import (
    ChatRequest,
    ChatResponse,
    CitationOut,
    SuggestResponse,
    TableDataOut,
)
from app.domain.ports.inbound.chat_use_case import ChatUseCase

router = APIRouter(prefix="/api", tags=["chat"])


@router.post("/chat", response_model=ChatResponse)
async def post_chat(
    body: ChatRequest,
    chat_uc: ChatUseCase = Depends(get_chat_use_case),
) -> ChatResponse:
    """Processa uma mensagem do usuário e retorna resposta estruturada."""
    message = await chat_uc.process_message(
        message=body.message,
        session_id=body.session_id,
        history=body.history,
    )

    sources = [
        CitationOut(
            document_title=c.document_title,
            document_url=c.document_url,
            snippet=c.snippet,
            page_number=c.page_number,
        )
        for c in message.sources
    ]

    tables = [
        TableDataOut(
            headers=t.headers,
            rows=t.rows,
            source_document=t.source_document,
            title=t.title,
            page_number=t.page_number,
        )
        for t in message.tables
    ]

    return ChatResponse(text=message.content, sources=sources, tables=tables)


@router.get("/suggest", response_model=SuggestResponse)
async def get_suggest(
    chat_uc: ChatUseCase = Depends(get_chat_use_case),
) -> SuggestResponse:
    """Retorna sugestões de perguntas iniciais."""
    suggestions = await chat_uc.get_suggestions()
    return SuggestResponse(suggestions=suggestions)
