"""Domain service: MultiAgentChatService — orquestrador do pipeline multi-agente.

Implementa ChatUseCase e ChatStreamUseCase.
Pipeline: busca → DataAgent → WriterAgent → ResponseAssembler.
Emite ProgressEvents via AsyncIterator (SSE).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from uuid import UUID

from app.domain.ports.inbound.chat_stream_use_case import ChatStreamUseCase
from app.domain.ports.inbound.chat_use_case import ChatUseCase
from app.domain.ports.outbound.analytics_repository import AnalyticsRepository
from app.domain.ports.outbound.search_repository import SearchRepository
from app.domain.ports.outbound.session_repository import SessionRepository
from app.domain.services.data_agent import DataAgent
from app.domain.services.prompt_builder import PromptBuilder
from app.domain.services.response_assembler import ResponseAssembler
from app.domain.services.table_validator import TableValidatorAgent
from app.domain.services.writer_agent import WriterAgent
from app.domain.value_objects.chat_message import ChatMessage
from app.domain.value_objects.progress_event import ProgressEvent

logger = logging.getLogger(__name__)

_DEFAULT_SUGGESTIONS = [
    "O que é o portal de transparência do TRE-PI?",
    "Quais licitações estão abertas?",
    "Como consultar os contratos vigentes?",
    "Qual o orçamento do TRE-PI?",
]


class MultiAgentChatService(ChatUseCase, ChatStreamUseCase):
    """Orquestra pipeline multi-agente com progresso via callback.

    Compatível com o endpoint JSON existente (ChatUseCase)
    e com o novo endpoint SSE (ChatStreamUseCase).
    """

    def __init__(
        self,
        search_repo: SearchRepository,
        session_repo: SessionRepository,
        analytics_repo: AnalyticsRepository,
        data_agent: DataAgent,
        writer_agent: WriterAgent,
        assembler: ResponseAssembler,
        prompt_builder: PromptBuilder | None = None,
        table_validator: TableValidatorAgent | None = None,
        top_k: int = 10,
    ) -> None:
        self._search = search_repo
        self._sessions = session_repo
        self._analytics = analytics_repo
        self._data_agent = data_agent
        self._writer_agent = writer_agent
        self._assembler = assembler
        self._builder = prompt_builder or PromptBuilder()
        self._table_validator = table_validator or TableValidatorAgent()
        self._top_k = top_k

    # ------------------------------------------------------------------
    # ChatUseCase (endpoint JSON — compatível com router existente)
    # ------------------------------------------------------------------

    async def process_message(
        self,
        message: str,
        session_id: UUID | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> ChatMessage:
        """Fluxo completo — retorna ChatMessage final."""
        start_ms = int(time.monotonic() * 1000)
        chat_message: ChatMessage | None = None

        async for event in self._run_pipeline(message, session_id, history):
            if event.event_type == "done":
                chat_message = event.data.get("chat_message")
            elif event.event_type == "error":
                raise RuntimeError(event.message)

        if chat_message is None:
            # Nunca deve acontecer, mas por segurança
            chat_message = ChatMessage(
                role="assistant",
                content="Não foi possível processar sua pergunta.",
                sources=[],
                tables=[],
            )

        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        # Persistência e analytics (igual ao ChatService)
        await self._persist_and_log(
            message=message,
            chat_message=chat_message,
            session_id=session_id,
            elapsed_ms=elapsed_ms,
        )

        return chat_message

    async def get_suggestions(self) -> list[str]:
        return list(_DEFAULT_SUGGESTIONS)

    # ------------------------------------------------------------------
    # ChatStreamUseCase (endpoint SSE)
    # ------------------------------------------------------------------

    async def process_message_stream(
        self,
        message: str,
        session_id: UUID | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> AsyncIterator[ProgressEvent]:
        """Fluxo SSE — yield ProgressEvent a cada etapa."""
        start_ms = int(time.monotonic() * 1000)
        chat_message: ChatMessage | None = None

        async for event in self._run_pipeline(message, session_id, history):
            if event.event_type == "done":
                chat_message = event.data.get("chat_message")
            yield event

        # Persistência e analytics após o stream terminar
        if chat_message is not None:
            elapsed_ms = int(time.monotonic() * 1000) - start_ms
            try:
                await self._persist_and_log(
                    message=message,
                    chat_message=chat_message,
                    session_id=session_id,
                    elapsed_ms=elapsed_ms,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Falha ao persistir sessão/analytics: %s", exc)

    # ------------------------------------------------------------------
    # Pipeline interno
    # ------------------------------------------------------------------

    async def _run_pipeline(
        self,
        message: str,
        session_id: UUID | None,
        history: list[dict[str, object]] | None,
    ) -> AsyncIterator[ProgressEvent]:
        """Executa o pipeline e emite eventos de progresso."""
        try:
            # 1. Busca contexto
            yield ProgressEvent(
                event_type="searching",
                message="Buscando informações relevantes...",
                data={"message": "Buscando informações relevantes..."},
            )
            pages = await self._search.search_pages(message, top_k=self._top_k * 2)
            chunks = await self._search.search_chunks(message, top_k=self._top_k)
            tables_raw = await self._search.search_tables(message)
            tables = self._table_validator.select_best_tables(tables_raw)

            # 2. DataAgent analisa
            yield ProgressEvent(
                event_type="analyzing",
                message=f"Analisando {len(tables)} tabela(s) encontrada(s)...",
                data={"message": f"Analisando dados...", "tables_found": len(tables)},
            )
            analysis = await self._data_agent.analyze(
                query=message,
                pages=pages,
                chunks=chunks,
                tables=tables,
            )

            # Emite eventos de tool calls para o frontend
            for log in analysis.tool_calls_log:
                result_str = log.get("result")
                yield ProgressEvent(
                    event_type="tool_call",
                    message=f"Calculando {log['tool']}...",
                    data={
                        "tool": log["tool"],
                        "args": log.get("args", {}),
                        "result": result_str,
                    },
                )

            # 3. WriterAgent redige
            yield ProgressEvent(
                event_type="writing",
                message="Preparando resposta...",
                data={"message": "Preparando resposta..."},
            )
            narrative = await self._writer_agent.write(query=message, analysis=analysis)

            # 4. Assembler monta o ChatMessage final
            chat_message = self._assembler.assemble(
                query=message,
                narrative=narrative,
                analysis=analysis,
            )

            # Serializa para o evento SSE
            chat_response_data = self._serialize_chat_message(chat_message)

            yield ProgressEvent(
                event_type="done",
                message="Resposta pronta.",
                data={**chat_response_data, "chat_message": chat_message},
            )

        except Exception as exc:  # noqa: BLE001
            logger.exception("Erro no pipeline multi-agente: %s", exc)
            yield ProgressEvent(
                event_type="error",
                message="Erro ao processar sua pergunta. Tente novamente.",
                data={"message": str(exc), "code": "PIPELINE_FAILURE"},
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _serialize_chat_message(self, msg: ChatMessage) -> dict:
        """Serializa ChatMessage para o formato JSON do endpoint SSE."""
        return {
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

    async def _persist_and_log(
        self,
        message: str,
        chat_message: ChatMessage,
        session_id: UUID | None,
        elapsed_ms: int,
    ) -> None:
        """Persiste mensagem na sessão e loga analytics."""
        intent = self._builder.classify_intent(message)

        if session_id is not None:
            try:
                session = await self._sessions.get(session_id)
                if session is not None:
                    user_msg = ChatMessage(role="user", content=message, sources=[], tables=[])
                    await self._sessions.save_message(session_id, user_msg)
                    await self._sessions.save_message(session_id, chat_message)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Falha ao salvar sessão: %s", exc)

        try:
            await self._analytics.log_query(
                session_id=session_id,
                query=message,
                intent_type=str(intent),
                pages_found=0,
                chunks_found=0,
                tables_found=len(chat_message.tables),
                response_time_ms=elapsed_ms,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Falha ao logar analytics: %s", exc)
