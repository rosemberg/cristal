"""Router: Chat — POST /api/chat, GET /api/suggest, GET /api/categories, GET /api/transparency-map."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from app.adapters.inbound.fastapi.dependencies import get_chat_use_case
from app.adapters.inbound.fastapi.schemas import (
    CategoriesResponse,
    CategoryItem,
    ChatRequest,
    ChatResponse,
    CitationOut,
    MetricItemOut,
    SuggestResponse,
    TableDataOut,
    TransparencyMapItem,
    TransparencyMapResponse,
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

    metrics = [MetricItemOut(label=m.label, value=m.value) for m in message.metrics]

    return ChatResponse(
        text=message.content,
        sources=sources,
        tables=tables,
        suggestions=message.suggestions,
        metrics=metrics,
    )


@router.get("/suggest", response_model=SuggestResponse)
async def get_suggest(
    chat_uc: ChatUseCase = Depends(get_chat_use_case),
) -> SuggestResponse:
    """Retorna sugestões de perguntas iniciais."""
    suggestions = await chat_uc.get_suggestions()
    return SuggestResponse(suggestions=suggestions)


@router.get("/categories", response_model=CategoriesResponse)
async def get_categories(request: Request) -> CategoriesResponse:
    """Lista categorias do portal de transparência com contagem de páginas."""
    search_repo = getattr(request.app.state, "search_repo", None)
    if search_repo is None:
        return CategoriesResponse(categories=[])
    try:
        cats = await search_repo.get_categories()
        return CategoriesResponse(
            categories=[
                CategoryItem(name=c["name"], page_count=int(c.get("count", 0)))  # type: ignore[arg-type]
                for c in cats
            ]
        )
    except Exception:  # noqa: BLE001
        return CategoriesResponse(categories=[])


@router.get("/transparency-map", response_model=TransparencyMapResponse)
async def get_transparency_map(request: Request) -> TransparencyMapResponse:
    """Retorna o mapa de transparência: categorias com contagem de páginas e documentos."""
    search_repo = getattr(request.app.state, "search_repo", None)
    if search_repo is None:
        return TransparencyMapResponse(categories=[], totals={})
    try:
        cats = await search_repo.get_categories()
        stats = await search_repo.get_stats()
        items = [
            TransparencyMapItem(
                category=c["name"],  # type: ignore[arg-type]
                page_count=int(c.get("count", 0)),  # type: ignore[arg-type]
            )
            for c in cats
        ]
        return TransparencyMapResponse(categories=items, totals=stats)
    except Exception:  # noqa: BLE001
        return TransparencyMapResponse(categories=[], totals={})
