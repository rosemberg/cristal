"""Domain service: WriterAgent — redige texto narrativo a partir de dados pré-computados.

Responsabilidade única: transformar o AnalysisResult em texto Markdown legível.
NUNCA faz cálculos — recebe dados já computados pelo DataAgent.
"""

from __future__ import annotations

import json
import logging
from decimal import Decimal

from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.services.data_agent import AnalysisResult

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Você é o WriterAgent do Cristal — assistente de transparência do TRE-PI.
Sua única responsabilidade é redigir um texto narrativo claro e conciso
a partir de dados JÁ COMPUTADOS que você receberá.

Regras:
- Use apenas os dados fornecidos. NÃO invente nem recalcule valores.
- Escreva em Markdown simples (negrito, listas). Sem tabelas no texto.
- Seja objetivo e direto. Máximo 3 parágrafos.
- Cite os valores exatos fornecidos (ex: "foram encontrados **51 contratos**").
- Não mencione ferramentas, APIs ou processos internos.
- Responda em português brasileiro.
"""


def _format_decimal(value: Decimal) -> str:
    """Formata Decimal como 'R$ 1.234.567,89'."""
    value = value.quantize(Decimal("0.01"))
    parts = str(value).split(".")
    integer_part = parts[0]
    decimal_part = parts[1] if len(parts) > 1 else "00"
    rev = integer_part[::-1]
    grouped = ".".join(rev[i: i + 3] for i in range(0, len(rev), 3))
    integer_fmt = grouped[::-1]
    return f"R$ {integer_fmt},{decimal_part}"


def _build_data_context(query: str, analysis: AnalysisResult) -> str:
    """Constrói o contexto de dados para o WriterAgent."""
    parts: list[str] = [f"Query do usuário: {query}\n"]

    # Resumo do DataAgent
    if analysis.data_summary:
        try:
            summary = json.loads(analysis.data_summary)
            findings = summary.get("key_findings", "")
            if findings:
                parts.append(f"Dados computados: {findings}\n")
        except (json.JSONDecodeError, ValueError):
            parts.append(f"Dados computados: {analysis.data_summary}\n")

    # Métricas calculadas
    if analysis.computed_metrics:
        parts.append("Métricas calculadas (valores exatos — use-os no texto):")
        for metric in analysis.computed_metrics:
            result_str = metric.result
            if isinstance(result_str, Decimal):
                result_str = _format_decimal(result_str)
            parts.append(f"  - {metric.tool_name}: {result_str}")

    # Tabelas selecionadas (resumo)
    if analysis.selected_tables:
        parts.append(f"\nTabelas disponíveis: {len(analysis.selected_tables)}")
        for t in analysis.selected_tables[:2]:
            data_rows = [r for r in t.rows if r and str(r[0]).strip().upper() not in {"TOTAL", "SUBTOTAL"}]
            parts.append(f"  - {t.caption or t.document_url}: {len(data_rows)} linhas")

    # Chunks textuais
    if analysis.relevant_chunks:
        parts.append("\nTrechos relevantes:")
        for cm in analysis.relevant_chunks[:2]:
            parts.append(f"  • {cm.chunk.text[:400]}")

    return "\n".join(parts)


class WriterAgent:
    """Agente escritor — redige texto narrativo a partir de dados pré-computados."""

    def __init__(self, llm: LLMGateway) -> None:
        self._llm = llm

    async def write(self, query: str, analysis: AnalysisResult) -> str:
        """Retorna o texto narrativo (Markdown) para o campo 'text' do ChatMessage."""
        context = _build_data_context(query, analysis)
        messages = [{"role": "user", "content": context}]

        try:
            text = await self._llm.generate(
                system_prompt=_SYSTEM_PROMPT,
                messages=messages,
                temperature=0.3,
            )
            return text.strip()
        except Exception as exc:  # noqa: BLE001
            logger.error("WriterAgent falhou: %s", exc)
            # Fallback: texto mínimo gerado a partir dos dados disponíveis
            return self._fallback_text(query, analysis)

    def _fallback_text(self, query: str, analysis: AnalysisResult) -> str:
        """Gera texto mínimo quando o LLM falha."""
        lines = [f"Resultado para: **{query}**\n"]
        for metric in analysis.computed_metrics:
            result_str = metric.result
            if isinstance(result_str, Decimal):
                result_str = _format_decimal(result_str)
            lines.append(f"- {metric.tool_name}: {result_str}")
        if not analysis.computed_metrics and analysis.relevant_chunks:
            lines.append(analysis.relevant_chunks[0].chunk.text[:500])
        return "\n".join(lines)
