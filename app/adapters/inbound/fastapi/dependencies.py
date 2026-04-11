"""Injeção de dependências via FastAPI Depends().

Cada função extrai o serviço correspondente de app.state, que é populado
durante o lifespan em app.py. Em testes, use app.dependency_overrides
para substituir por fakes sem lifespan real.
"""

from __future__ import annotations

from fastapi import Request

from app.domain.ports.inbound.chat_use_case import ChatUseCase
from app.domain.ports.inbound.document_use_case import DocumentUseCase
from app.domain.ports.inbound.session_use_case import SessionUseCase
from app.domain.ports.outbound.analytics_repository import AnalyticsRepository


async def get_analytics_repo(request: Request) -> AnalyticsRepository:
    """Retorna o AnalyticsRepository registrado no estado da aplicação."""
    return request.app.state.analytics_repo  # type: ignore[no-any-return]


async def get_chat_use_case(request: Request) -> ChatUseCase:
    """Retorna o ChatUseCase registrado no estado da aplicação."""
    return request.app.state.chat_service  # type: ignore[no-any-return]


async def get_document_use_case(request: Request) -> DocumentUseCase:
    """Retorna o DocumentUseCase registrado no estado da aplicação."""
    return request.app.state.document_service  # type: ignore[no-any-return]


async def get_session_use_case(request: Request) -> SessionUseCase:
    """Retorna o SessionUseCase registrado no estado da aplicação."""
    return request.app.state.session_service  # type: ignore[no-any-return]
