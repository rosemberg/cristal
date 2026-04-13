"""Domain service: DataAgent — LLM + function calling para análise de dados.

Responsabilidade única: interpretar a query do usuário e invocar tools Python
para extrair/computar dados de tabelas. NUNCA faz aritmética diretamente.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.domain.entities.document_table import DocumentTable
from app.domain.ports.outbound.function_calling_gateway import (
    FunctionCallingGateway,
    FunctionCallingResponse,
)
from app.domain.services.prompt_builder import _extract_relevant_section
from app.domain.services.tool_declarations import TOOL_DECLARATIONS
from app.domain.services.tool_executor import ToolExecutor, ToolResult
from app.domain.value_objects.search_result import ChunkMatch, PageMatch

# Limite de chars de main_content por página no contexto do DataAgent
# (menor que PromptBuilder para controlar tamanho do contexto de function calling)
_MAX_PAGE_CHARS_DATA_AGENT = 8_000


logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_TEMPLATE = """\
Você é o DataAgent do Cristal — assistente de transparência do TRE-PI.
Sua única responsabilidade é analisar a query do usuário e usar as tools
disponíveis para extrair e computar dados de tabelas.

Regras:
- NUNCA calcule mentalmente. Use sempre as tools para contagens e somas.
- Se há tabelas no contexto, invoque as tools relevantes antes de concluir.
- Ao finalizar, produza um data_summary em JSON com:
  {{"selected_table_indices": [...], "key_findings": "...", "query_answered": true/false}}
- Responda apenas em JSON válido quando for o texto final.
- Máximo de %d rodadas de tool calls.
"""


@dataclass
class AnalysisResult:
    """Saída estruturada do DataAgent — entrada do WriterAgent e ResponseAssembler."""

    selected_tables: list[DocumentTable]
    computed_metrics: list[ToolResult]
    relevant_chunks: list[ChunkMatch]   # chunks selecionados (com título e URL)
    data_summary: str                   # resumo gerado pelo DataAgent para o Writer
    tool_calls_log: list[dict[str, Any]] = field(default_factory=list)  # auditoria


def _table_to_dict(table: DocumentTable) -> dict[str, Any]:
    """Converte DocumentTable para dict compatível com ToolExecutor."""
    return {
        "headers": list(table.headers),
        "rows": [list(r) for r in table.rows],
    }


def _build_context_message(
    query: str,
    pages: list[PageMatch],
    chunks: list[ChunkMatch],
    tables: list[DocumentTable],
) -> str:
    """Monta a mensagem de contexto enviada ao DataAgent.

    Inclui (em ordem):
    1. Tabelas estruturadas (se existirem) — melhor para cálculos
    2. Trechos de páginas com main_content relevante — cobre dados não tabelados
    3. Chunks textuais de documentos

    As páginas são incluídas porque muitos dados do portal TRE-PI existem
    apenas no main_content (ex: listagem de contratos) e nunca como tabelas
    em document_tables.
    """
    parts: list[str] = [f"Query do usuário: {query}\n"]

    if tables:
        parts.append(f"Tabelas disponíveis ({len(tables)}):")
        for i, t in enumerate(tables):
            header_str = " | ".join(t.headers)
            parts.append(f"  Tabela {i} — {t.caption or t.document_url}")
            parts.append(f"  Cabeçalhos: {header_str}")
            parts.append(f"  Linhas: {len(t.rows)}")

    # Inclui excerpts das páginas com main_content substancial
    pages_with_content = [
        m for m in pages
        if m.page.main_content and len(m.page.main_content) > 500
    ]
    if pages_with_content:
        parts.append(f"\nConteúdo de páginas ({len(pages_with_content)} com dados):")
        for match in pages_with_content[:5]:
            p = match.page
            excerpt = _extract_relevant_section(p.main_content, query)
            # Limita para não exceder o contexto do DataAgent
            excerpt = excerpt[:_MAX_PAGE_CHARS_DATA_AGENT].strip()
            if excerpt:
                parts.append(f"\n  === {p.title} ({p.url}) ===")
                parts.append(f"  {excerpt}")

    if chunks:
        parts.append(f"\nTrechos relevantes ({min(3, len(chunks))}):")
        for cm in chunks[:3]:
            parts.append(f"  • {cm.chunk.text[:300]}")

    return "\n".join(parts)


class DataAgent:
    """Agente de dados — LLM + function calling.

    Invoca tools Python via loop até atingir max_tool_rounds ou o LLM
    indicar que terminou (finish_reason="stop").
    """

    def __init__(
        self,
        llm: FunctionCallingGateway,
        tool_executor: ToolExecutor,
        max_tool_rounds: int = 5,
    ) -> None:
        self._llm = llm
        self._executor = tool_executor
        self._max_tool_rounds = max_tool_rounds

    async def analyze(
        self,
        query: str,
        pages: list[PageMatch],
        chunks: list[ChunkMatch],
        tables: list[DocumentTable],
    ) -> AnalysisResult:
        """Analisa a query invocando tools até obter resposta final."""
        tables_as_dicts = [_table_to_dict(t) for t in tables]
        tool_calls_log: list[dict[str, Any]] = []
        computed_metrics: list[ToolResult] = []

        system_prompt = _SYSTEM_PROMPT_TEMPLATE % self._max_tool_rounds
        context_msg = _build_context_message(query, pages, chunks, tables)

        # Histórico de mensagens para o loop de function calling
        messages: list[dict[str, Any]] = [
            {"role": "user", "content": context_msg},
        ]

        data_summary = ""
        selected_table_indices: list[int] = []

        for round_num in range(self._max_tool_rounds):
            response: FunctionCallingResponse = await self._llm.generate_with_tools(
                system_prompt=system_prompt,
                messages=messages,
                tools=TOOL_DECLARATIONS,
                temperature=0.1,
            )

            if response.finish_reason == "function_call" and response.function_calls:
                # Processa todas as function calls desta rodada
                tool_results_content: list[dict[str, Any]] = []

                for fc in response.function_calls:
                    tool_result = self._executor.execute(
                        tool_name=fc.name,
                        args=fc.args,
                        tables=tables_as_dicts,
                    )
                    computed_metrics.append(tool_result)

                    result_value = tool_result.result
                    # Serializa Decimal para string
                    if hasattr(result_value, "quantize"):
                        result_value = str(result_value)

                    log_entry = {
                        "round": round_num,
                        "tool": fc.name,
                        "args": fc.args,
                        "result": result_value,
                        "metadata": tool_result.metadata,
                    }
                    tool_calls_log.append(log_entry)
                    logger.debug("DataAgent tool call: %s", log_entry)

                    tool_results_content.append({
                        "type": "function_response",
                        "name": fc.name,
                        "response": result_value,
                    })

                    # Rastreia quais tabelas foram consultadas
                    table_idx = fc.args.get("table_index")
                    if table_idx is not None and int(table_idx) not in selected_table_indices:
                        selected_table_indices.append(int(table_idx))

                # Adiciona ao histórico: model function_call + user function_response
                messages.append({
                    "role": "assistant",
                    "content": [
                        {"type": "function_call", "name": fc.name, "args": fc.args}
                        for fc in response.function_calls
                    ],
                })
                messages.append({
                    "role": "user",
                    "content": tool_results_content,
                })

            else:
                # LLM finalizou — extrai data_summary do texto
                raw_text = response.text or ""
                data_summary = self._extract_summary(raw_text)
                # Só aceita índices do data_summary se o DataAgent fez ao menos
                # um tool call. Sem tool calls → pergunta não é sobre dados tabulares.
                if tool_calls_log:
                    selected_table_indices = self._extract_selected_indices(
                        data_summary, selected_table_indices, len(tables)
                    )
                else:
                    selected_table_indices = []
                break

        # Monta selected_tables apenas com os índices que o DataAgent consultou
        # via tool calls. Perguntas informacionais (sem tool calls) não exibem tabelas.
        selected_tables = [
            tables[i] for i in selected_table_indices if i < len(tables)
        ]

        # Chunks relevantes: top-3 por score — o ResponseAssembler filtra por score
        # antes de exibir como fonte, preservando o contexto completo para o WriterAgent.
        relevant_chunks = sorted(chunks, key=lambda c: c.score, reverse=True)[:3]

        return AnalysisResult(
            selected_tables=selected_tables,
            computed_metrics=computed_metrics,
            relevant_chunks=relevant_chunks,
            data_summary=data_summary,
            tool_calls_log=tool_calls_log,
        )

    def _extract_summary(self, raw_text: str) -> str:
        """Tenta extrair o JSON do data_summary ou usa o texto bruto."""
        raw_text = raw_text.strip()
        # Tenta parse direto
        try:
            parsed = json.loads(raw_text)
            if isinstance(parsed, dict):
                return json.dumps(parsed, ensure_ascii=False)
        except (json.JSONDecodeError, ValueError):
            pass
        # Tenta extrair JSON de bloco markdown
        import re
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_text, re.DOTALL)
        if m:
            try:
                parsed2 = json.loads(m.group(1))
                if isinstance(parsed2, dict):
                    return json.dumps(parsed2, ensure_ascii=False)
            except (json.JSONDecodeError, ValueError):
                pass
        return raw_text

    @staticmethod
    def _extract_selected_indices(
        summary: str,
        already_selected: list[int],
        total_tables: int,
    ) -> list[int]:
        """Extrai selected_table_indices do data_summary se disponível."""
        try:
            parsed = json.loads(summary)
            indices = parsed.get("selected_table_indices", [])
            if isinstance(indices, list):
                return [int(i) for i in indices if int(i) < total_tables]
        except (json.JSONDecodeError, ValueError, TypeError):
            pass
        return already_selected

