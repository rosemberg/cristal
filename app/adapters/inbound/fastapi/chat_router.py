"""Router: Chat — POST /api/chat, POST /api/chat/stream (SSE), GET /api/suggest, etc."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

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
            headers=[str(h) if h is not None else "" for h in t.headers],
            rows=[[str(c) if c is not None else "" for c in row] for row in t.rows],
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


@router.post("/chat/stream")
async def post_chat_stream(
    body: ChatRequest,
    request: Request,
) -> StreamingResponse:
    """Chat com SSE — emite ProgressEvents em tempo real.

    Usa fetch() + ReadableStream no frontend (POST não suportado por EventSource nativo).
    Fallback gracioso: se SSE não estiver disponível, retorna evento único "done".
    """
    chat_service = getattr(request.app.state, "chat_service", None)
    settings = getattr(request.app.state, "settings", None)
    sse_enabled = getattr(settings, "sse_enabled", True) if settings else True

    async def event_generator():
        # Keepalive inicial para evitar timeout de proxy
        yield ": keepalive\n\n"

        if not sse_enabled or not hasattr(chat_service, "process_message_stream"):
            # Fallback: executa síncrono e retorna como SSE com evento único
            try:
                from app.adapters.inbound.fastapi.dependencies import get_chat_use_case
                msg = await chat_service.process_message(
                    message=body.message,
                    session_id=body.session_id,
                    history=body.history,
                )
                payload = {
                    "text": msg.content,
                    "sources": [
                        {
                            "document_title": c.document_title,
                            "document_url": c.document_url,
                            "snippet": c.snippet,
                            "page_number": c.page_number,
                        }
                        for c in msg.sources
                    ],
                    "tables": [
                        {
                            "headers": t.headers,
                            "rows": t.rows,
                            "source_document": t.source_document,
                            "title": t.title,
                            "page_number": t.page_number,
                        }
                        for t in msg.tables
                    ],
                    "metrics": [{"label": m.label, "value": m.value} for m in msg.metrics],
                    "suggestions": list(msg.suggestions),
                }
                yield f"event: done\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
            except Exception as exc:  # noqa: BLE001
                error_payload = {"message": str(exc), "code": "FALLBACK_FAILURE"}
                yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
            return

        # Pipeline multi-agente com streaming real
        keepalive_seconds = getattr(settings, "sse_keepalive_seconds", 15) if settings else 15
        import time as _time
        last_keepalive = _time.monotonic()

        try:
            async for event in chat_service.process_message_stream(
                message=body.message,
                session_id=body.session_id,
                history=body.history,
            ):
                # Remove chat_message do payload (não serializável via JSON diretamente)
                data = {k: v for k, v in event.data.items() if k != "chat_message"}
                yield f"event: {event.event_type}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"

                # Keepalive entre eventos
                now = _time.monotonic()
                if now - last_keepalive >= keepalive_seconds:
                    yield ": keepalive\n\n"
                    last_keepalive = now

        except Exception as exc:  # noqa: BLE001
            error_payload = {"message": str(exc), "code": "STREAM_FAILURE"}
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # evita buffering no nginx/OpenShift
        },
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
