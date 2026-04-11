"""Router: Sessions — POST/GET /api/sessions/..."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.adapters.inbound.fastapi.dependencies import get_session_use_case
from app.adapters.inbound.fastapi.schemas import (
    CitationOut,
    MessageOut,
    MessagesResponse,
    SessionCreateRequest,
    SessionOut,
    TableDataOut,
)
from app.domain.ports.inbound.session_use_case import SessionUseCase

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut, status_code=201)
async def create_session(
    body: SessionCreateRequest,
    session_uc: SessionUseCase = Depends(get_session_use_case),
) -> SessionOut:
    """Cria uma nova sessão de chat."""
    session = await session_uc.create(title=body.title)
    return SessionOut(
        id=session.id,
        created_at=session.created_at,
        last_active=session.last_active,
        title=session.title,
    )


@router.get("/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: UUID,
    session_uc: SessionUseCase = Depends(get_session_use_case),
) -> SessionOut:
    """Retorna dados de uma sessão pelo ID."""
    session = await session_uc.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return SessionOut(
        id=session.id,
        created_at=session.created_at,
        last_active=session.last_active,
        title=session.title,
    )


@router.get("/{session_id}/messages", response_model=MessagesResponse)
async def list_messages(
    session_id: UUID,
    session_uc: SessionUseCase = Depends(get_session_use_case),
) -> MessagesResponse:
    """Lista as mensagens de uma sessão."""
    messages = await session_uc.list_messages(session_id)
    return MessagesResponse(
        messages=[
            MessageOut(
                role=m.role,
                content=m.content,
                sources=[
                    CitationOut(
                        document_title=c.document_title,
                        document_url=c.document_url,
                        snippet=c.snippet,
                        page_number=c.page_number,
                    )
                    for c in m.sources
                ],
                tables=[
                    TableDataOut(
                        headers=t.headers,
                        rows=t.rows,
                        source_document=t.source_document,
                        title=t.title,
                        page_number=t.page_number,
                    )
                    for t in m.tables
                ],
            )
            for m in messages
        ]
    )
