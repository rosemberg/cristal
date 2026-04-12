"""Application service: ChatService — implements ChatUseCase."""

from __future__ import annotations

import json
import re
import time
from decimal import Decimal, InvalidOperation
from uuid import UUID

from app.domain.ports.inbound.chat_use_case import ChatUseCase
from app.domain.ports.outbound.analytics_repository import AnalyticsRepository
from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.ports.outbound.search_repository import SearchRepository
from app.domain.ports.outbound.session_repository import SessionRepository
from app.domain.services.prompt_builder import PromptBuilder
from app.domain.services.table_validator import TableValidatorAgent
from app.domain.value_objects.chat_message import ChatMessage, Citation, MetricItem, TableData
from app.domain.value_objects.intent import QueryIntent

# ---------------------------------------------------------------------------
# Default suggestions exposed when no specific context is found
# ---------------------------------------------------------------------------

_DEFAULT_SUGGESTIONS = [
    "O que é o portal de transparência do TRE-PI?",
    "Quais licitações estão abertas?",
    "Como consultar os contratos vigentes?",
    "Qual o orçamento do TRE-PI?",
    "Como acessar informações sobre servidores?",
]

# Regex to extract JSON from markdown code fences or raw JSON blocks
_JSON_PATTERN = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```|(\{.*\})", re.DOTALL)

# Regex que aceita "R$ 1.234.567,89", "1.234.567,89", "1234567.89" etc.
_BRL_PATTERN = re.compile(r"[\d.,]+")


def _parse_brl(value: str) -> Decimal | None:
    """Converte string monetária brasileira para Decimal. Retorna None se inválida."""
    raw = value.strip().lstrip("R$").strip()
    m = _BRL_PATTERN.search(raw)
    if not m:
        return None
    s = m.group()
    # Formato BR: pontos como milhar, vírgula como decimal → "1.234.567,89"
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    # Senão: assume ponto já é decimal
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _sum_value_column(rows: list[list[object]], col_idx: int) -> Decimal | None:
    """Soma todos os valores da coluna col_idx, ignorando células não-numéricas."""
    total = Decimal(0)
    found_any = False
    for row in rows:
        if col_idx >= len(row):
            continue
        val = _parse_brl(str(row[col_idx]))
        if val is not None:
            total += val
            found_any = True
    return total if found_any else None


def _format_brl(value: Decimal) -> str:
    """Formata Decimal como 'R$ 1.234.567,89'."""
    # Arredonda para 2 casas
    value = value.quantize(Decimal("0.01"))
    # Separa inteiro e decimal
    parts = str(value).split(".")
    integer_part = parts[0]
    decimal_part = parts[1] if len(parts) > 1 else "00"
    # Insere pontos a cada 3 dígitos no inteiro
    rev = integer_part[::-1]
    grouped = ".".join(rev[i:i+3] for i in range(0, len(rev), 3))
    integer_fmt = grouped[::-1]
    return f"R$ {integer_fmt},{decimal_part}"


class ChatService(ChatUseCase):
    """
    Orchestrates the full chat pipeline:
    1. Classify intent
    2. Search relevant context (pages, chunks, tables)
    3. Build prompt with context
    4. Call LLM
    5. Parse structured response
    6. Persist message to session (if session_id provided)
    7. Log analytics
    """

    def __init__(
        self,
        search_repo: SearchRepository,
        session_repo: SessionRepository,
        analytics_repo: AnalyticsRepository,
        llm: LLMGateway,
        prompt_builder: PromptBuilder | None = None,
        table_validator: TableValidatorAgent | None = None,
        top_k: int = 10,
    ) -> None:
        self._search = search_repo
        self._sessions = session_repo
        self._analytics = analytics_repo
        self._llm = llm
        self._builder = prompt_builder or PromptBuilder()
        self._table_validator = table_validator or TableValidatorAgent()
        self._top_k = top_k

    async def process_message(
        self,
        message: str,
        session_id: UUID | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> ChatMessage:
        start_ms = int(time.monotonic() * 1000)

        # 1. Classify intent
        intent = self._builder.classify_intent(message)

        # 2. Search context
        # Páginas recebem top_k maior pois carregam main_content extenso
        pages = await self._search.search_pages(message, top_k=self._top_k * 2)
        chunks = await self._search.search_chunks(message, top_k=self._top_k)
        tables = await self._search.search_tables(message)

        # 2b. Validate, deduplicate and rank tables
        tables = self._table_validator.select_best_tables(tables)

        # 3. Build prompt
        system_prompt = self._builder.build_system_prompt()
        context = self._builder.build_context(pages=pages, chunks=chunks, tables=tables, query=message)

        formatted_history = self._builder.format_history(history)
        llm_messages: list[dict[str, object]] = [
            *formatted_history,
            {"role": "user", "content": f"{context}\n\n{message}" if context else message},
        ]

        # 4. Call LLM
        raw = await self._llm.generate(system_prompt=system_prompt, messages=llm_messages)

        # 5. Parse response
        chat_message = self._parse_llm_response(raw)

        elapsed_ms = int(time.monotonic() * 1000) - start_ms

        # 6. Persist to session if session_id provided
        if session_id is not None:
            session = await self._sessions.get(session_id)
            if session is not None:
                user_msg = ChatMessage(role="user", content=message, sources=[], tables=[])
                await self._sessions.save_message(session_id, user_msg)
                await self._sessions.save_message(session_id, chat_message)

        # 7. Log analytics
        await self._analytics.log_query(
            session_id=session_id,
            query=message,
            intent_type=str(intent),
            pages_found=len(pages),
            chunks_found=len(chunks),
            tables_found=len(tables),
            response_time_ms=elapsed_ms,
        )

        return chat_message

    async def get_suggestions(self) -> list[str]:
        return list(_DEFAULT_SUGGESTIONS)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_llm_response(self, raw: str) -> ChatMessage:
        """Parse LLM JSON response into ChatMessage. Falls back to plain text."""
        data = self._extract_json(raw)
        if data is None:
            return ChatMessage(role="assistant", content=raw, sources=[], tables=[])

        text = data.get("text") or data.get("content") or raw
        raw_sources = data.get("sources") or []
        raw_tables = data.get("tables") or []
        raw_suggestions = data.get("suggestions") or []
        raw_metrics = data.get("metrics") or []

        sources: list[Citation] = []
        for s in raw_sources if isinstance(raw_sources, list) else []:
            if isinstance(s, dict):
                sources.append(
                    Citation(
                        document_title=str(s.get("document_title") or ""),
                        document_url=str(s.get("document_url") or ""),
                        snippet=str(s.get("snippet") or ""),
                        page_number=int(s["page_number"]) if s.get("page_number") is not None else None,
                    )
                )

        tables: list[TableData] = []
        for t in raw_tables if isinstance(raw_tables, list) else []:
            if isinstance(t, dict):
                headers = t.get("headers") or []
                rows = t.get("rows") or []
                tables.append(
                    TableData(
                        headers=list(headers) if isinstance(headers, list) else [],
                        rows=[list(r) for r in rows if isinstance(r, list)] if isinstance(rows, list) else [],
                        source_document=str(t.get("source_document") or ""),
                        title=str(t["title"]) if t.get("title") is not None else None,
                        page_number=int(t["page_number"]) if t.get("page_number") is not None else None,
                    )
                )

        suggestions = [str(s) for s in raw_suggestions if isinstance(s, str)] if isinstance(raw_suggestions, list) else []

        metrics: list[MetricItem] = []
        for m in raw_metrics if isinstance(raw_metrics, list) else []:
            if isinstance(m, dict):
                metrics.append(
                    MetricItem(
                        label=str(m.get("label") or ""),
                        value=str(m.get("value") or ""),
                    )
                )

        metrics = self._fix_metrics_from_tables(metrics, tables)

        return ChatMessage(role="assistant", content=str(text), sources=sources, tables=tables, suggestions=suggestions, metrics=metrics)

    @staticmethod
    def _fix_metrics_from_tables(
        metrics: list[MetricItem],
        tables: list[TableData],
    ) -> list[MetricItem]:
        """Recalcula métricas de contagem e soma a partir das linhas reais da tabela.

        O LLM pode gerar métricas incorretas (conta/soma baseada no texto da página
        em vez das linhas retornadas). Este método substitui os valores de:
        - "Total de contratos" / "Total de registros" → contagem real de linhas
        - "Valor total" → soma real da coluna de valor da tabela

        Apenas corrige métricas quando há exatamente uma tabela com coluna de valor.
        """
        if not tables or not metrics:
            return metrics

        # Usa apenas a primeira tabela para correção (caso mais comum)
        table = tables[0]
        headers = [h.lower().strip() for h in table.headers]

        # Encontra índice da coluna de valor monetário
        _VALUE_NAMES = {"valor", "value", "valor total", "total", "preço", "preco", "montante"}
        value_col: int | None = None
        for i, h in enumerate(headers):
            if h in _VALUE_NAMES:
                value_col = i
                break

        # Filtra linhas de totalizador (linha TOTAL adicionada pelo LLM)
        data_rows = [
            row for row in table.rows
            if row and str(row[0]).strip().upper() not in {"TOTAL", "SUBTOTAL", "GRAND TOTAL"}
        ]
        row_count = len(data_rows)

        # Substitui métrica de contagem
        updated: list[MetricItem] = []
        count_labels = {"total de contratos", "total de registros", "contratos", "registros", "quantidade"}
        value_labels = {"valor total", "valor", "total"}
        count_updated = False
        value_updated = False

        for m in metrics:
            label_lower = m.label.lower().strip()
            if not count_updated and label_lower in count_labels:
                updated.append(MetricItem(label=m.label, value=str(row_count)))
                count_updated = True
                continue
            if not value_updated and value_col is not None and label_lower in value_labels:
                total = _sum_value_column(data_rows, value_col)
                if total is not None:
                    updated.append(MetricItem(label=m.label, value=_format_brl(total)))
                    value_updated = True
                    continue
            updated.append(m)

        return updated

    @staticmethod
    def _extract_json(raw: str) -> dict[str, object] | None:
        """Try to parse JSON from raw LLM output. Returns None if impossible."""
        raw = raw.strip()
        # Direct parse
        try:
            parsed: object = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown fence or loose braces
        m = _JSON_PATTERN.search(raw)
        if m:
            candidate = m.group(1) or m.group(2)
            if candidate:
                try:
                    parsed2: object = json.loads(candidate)
                    if isinstance(parsed2, dict):
                        return parsed2
                except json.JSONDecodeError:
                    pass

        return None
