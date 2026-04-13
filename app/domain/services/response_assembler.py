"""Domain service: ResponseAssembler — fonte única de verdade para ChatMessage.

Responsabilidade única: montar o ChatMessage final a partir de AnalysisResult
e do texto narrativo do WriterAgent. Zero LLM calls.

Garante:
- TOTAL único por tabela (nunca duplicado)
- Métricas derivadas dos ToolResults (não do texto)
- Citations extraídas dos chunks
- text não repete dados tabulares (sanitização básica)
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from app.domain.entities.document_table import DocumentTable
from app.domain.services.data_agent import AnalysisResult
from app.domain.services.tool_executor import ToolResult
from app.domain.value_objects.chat_message import ChatMessage, Citation, MetricItem, TableData

_TOTAL_LABELS = {"TOTAL", "SUBTOTAL", "GRAND TOTAL", "TOTAIS", "TOTAL GERAL"}

# Threshold de score para exibir um chunk como fonte.
# Perguntas informacionais (sem computed_metrics): rigoroso — chunks de contratos/passagens
# retornados por similaridade superficial não devem virar "FONTES" para "o que é o portal?".
_CITATION_MIN_SCORE_INFO: float = 0.35
# Perguntas de dados (com computed_metrics): relaxado — corpus é específico e scores menores
# ainda representam conteúdo relevante que justifica aparecer como fonte.
_CITATION_MIN_SCORE_DATA: float = 0.20

_DEFAULT_SUGGESTIONS = [
    "Quais contratos estão vigentes?",
    "Qual o valor total dos contratos?",
    "Liste os contratos por valor decrescente.",
    "Quais licitações estão abertas?",
]


def _format_brl(value: Decimal) -> str:
    """Formata Decimal como 'R$ 1.234.567,89'."""
    value = value.quantize(Decimal("0.01"))
    parts = str(value).split(".")
    integer_part = parts[0]
    decimal_part = parts[1] if len(parts) > 1 else "00"
    rev = integer_part[::-1]
    grouped = ".".join(rev[i: i + 3] for i in range(0, len(rev), 3))
    integer_fmt = grouped[::-1]
    return f"R$ {integer_fmt},{decimal_part}"


def _to_brl_if_decimal(value: object) -> str:
    """Converte Decimal para BRL; outros tipos retornam str."""
    if isinstance(value, Decimal):
        return _format_brl(value)
    return str(value)


def _label_for_tool(tool_result: ToolResult) -> str:
    """Gera label legível para um ToolResult."""
    col = tool_result.metadata.get("column", "")
    match tool_result.tool_name:
        case "count_rows":
            filter_col = tool_result.metadata.get("column")
            filter_val = tool_result.metadata.get("value")
            if filter_col and filter_val:
                return f"Total de registros ({filter_col}={filter_val})"
            return "Total de registros"
        case "sum_column":
            return f"Valor total ({col})" if col else "Valor total"
        case "filter_rows":
            return f"Linhas filtradas ({col})"
        case "sort_rows":
            return f"Ordenado por {col}"
        case "extract_value":
            target = tool_result.metadata.get("target_column", "")
            return f"Valor de {target}"
        case _:
            return tool_result.tool_name


class ResponseAssembler:
    """Monta ChatMessage final — Python puro, zero LLM."""

    def assemble(
        self,
        query: str,
        narrative: str,
        analysis: AnalysisResult,
    ) -> ChatMessage:
        """Monta ChatMessage garantindo fonte única de verdade.

        1. Converte selected_tables → TableData[] com linha TOTAL única
        2. Converte computed_metrics → MetricItem[]
        3. Extrai Citations dos chunks
        4. Sanitiza narrative para não duplicar dados tabulares
        5. Gera suggestions baseadas no contexto
        """
        tables = self._build_tables(analysis)
        metrics = self._build_metrics(analysis)
        sources = self._build_citations(analysis)
        clean_text = self._sanitize_narrative(narrative, tables)
        suggestions = self._build_suggestions(query, analysis)

        return ChatMessage(
            role="assistant",
            content=clean_text,
            sources=sources,
            tables=tables,
            suggestions=suggestions,
            metrics=metrics,
        )

    # ------------------------------------------------------------------
    # Internal builders
    # ------------------------------------------------------------------

    def _build_tables(self, analysis: AnalysisResult) -> list[TableData]:
        """Converte DocumentTable[] → TableData[] com linha TOTAL controlada."""
        result: list[TableData] = []
        for table in analysis.selected_tables:
            data_rows = self._get_data_rows(table)
            total_row = self._compute_total_row(table, data_rows)

            final_rows = data_rows
            if total_row is not None:
                final_rows = data_rows + [total_row]

            result.append(
                TableData(
                    headers=list(table.headers),
                    rows=[[str(c) for c in row] for row in final_rows],
                    source_document=table.document_url,
                    title=table.caption,
                    page_number=table.page_number,
                )
            )
        return result

    def _get_data_rows(self, table: DocumentTable) -> list[list[str]]:
        """Retorna apenas linhas de dados (exclui linhas de TOTAL existentes)."""
        return [
            [str(c) for c in row]
            for row in table.rows
            if row and str(row[0]).strip().upper() not in _TOTAL_LABELS
        ]

    def _compute_total_row(
        self,
        table: DocumentTable,
        data_rows: list[list[str]],
    ) -> list[str] | None:
        """Computa linha TOTAL para colunas monetárias — se houver alguma."""
        if not data_rows or not table.headers:
            return None

        # Identifica colunas numéricas/monetárias
        num_cols: list[int] = []
        for i, header in enumerate(table.headers):
            if any(
                kw in header.lower()
                for kw in ("valor", "value", "total", "preço", "preco", "montante", "custo")
            ):
                num_cols.append(i)

        if not num_cols:
            return None

        # Monta linha TOTAL
        total_row = [""] * len(table.headers)
        total_row[0] = "TOTAL"
        has_total = False

        for col_idx in num_cols:
            total = Decimal(0)
            found_any = False
            for row in data_rows:
                if col_idx < len(row):
                    v = self._parse_numeric(row[col_idx])
                    if v is not None:
                        total += v
                        found_any = True
            if found_any:
                total_row[col_idx] = _format_brl(total)
                has_total = True

        return total_row if has_total else None

    def _build_metrics(self, analysis: AnalysisResult) -> list[MetricItem]:
        """Converte ToolResults computados em MetricItem[] legíveis."""
        seen_labels: set[str] = set()
        metrics: list[MetricItem] = []

        for tool_result in analysis.computed_metrics:
            # Ignora tool calls sem resultado ou resultados de lista (filter/sort)
            if tool_result.result is None:
                continue
            if isinstance(tool_result.result, list):
                continue
            if "error" in tool_result.metadata:
                continue

            label = _label_for_tool(tool_result)
            if label in seen_labels:
                continue
            seen_labels.add(label)

            metrics.append(
                MetricItem(
                    label=label,
                    value=_to_brl_if_decimal(tool_result.result),
                )
            )

        return metrics

    def _build_citations(self, analysis: AnalysisResult) -> list[Citation]:
        """Constrói Citations a partir das tabelas selecionadas e chunks.

        Usa threshold adaptativo de score:
        - Pergunta de dados (computed_metrics não vazio): threshold relaxado (0.20).
        - Pergunta informacional (sem métricas): threshold rigoroso (0.35) — evita
          que contratos/passagens apareçam como "FONTES" para "o que é o portal?".

        Tabelas só são citadas se houve ao menos uma métrica computada (tool call
        bem-sucedido), garantindo que tabelas incidentais não virem fontes.
        """
        seen_urls: set[str] = set()
        citations: list[Citation] = []

        is_data_query = bool(analysis.computed_metrics)
        min_score = _CITATION_MIN_SCORE_DATA if is_data_query else _CITATION_MIN_SCORE_INFO

        # Citations das tabelas — apenas se houve análise efetiva (tool calls)
        if is_data_query:
            for table in analysis.selected_tables:
                url = table.document_url or ""
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                citations.append(
                    Citation(
                        document_title=table.caption or url,
                        document_url=url,
                        snippet=f"Tabela com {len(table.rows)} linhas",
                        page_number=table.page_number,
                    )
                )

        # Citations dos chunks relevantes — filtro de score adaptativo
        for cm in analysis.relevant_chunks:
            if cm.score < min_score:
                continue
            url = cm.document_url or cm.chunk.document_url or ""
            if url and url in seen_urls:
                continue
            seen_urls.add(url or cm.chunk.text[:50])
            citations.append(
                Citation(
                    document_title=cm.document_title or url or "Documento",
                    document_url=url,
                    snippet=cm.chunk.text[:200].replace("\n", " ").strip(),
                )
            )

        return citations

    def _sanitize_narrative(self, text: str, tables: list[TableData]) -> str:
        """Remove dados tabulares duplicados do texto narrativo.

        Estratégia: se o texto contém uma linha inteira de cabeçalho de tabela,
        remove o bloco correspondente. Não altera o texto se não houver duplicação.
        """
        if not tables:
            return text

        for table in tables:
            if not table.headers:
                continue
            # Procura pelo cabeçalho da tabela no texto (markdown pipe format)
            header_pattern = re.compile(
                r"\|[^\n]+\|[\s\S]*?\n(?:\|[-|: ]+\|[\s\S]*?\n)?(?:\|[^\n]+\|[\s\S]*?\n)*",
                re.MULTILINE,
            )
            text = header_pattern.sub("", text)
            break  # uma passagem é suficiente para a maioria dos casos

        return text.strip()

    def _build_suggestions(self, query: str, analysis: AnalysisResult) -> list[str]:
        """Gera sugestões de follow-up baseadas no contexto."""
        suggestions: list[str] = []

        # Sugestões baseadas nas tabelas encontradas
        for table in analysis.selected_tables[:1]:
            if table.headers:
                col_names = [h for h in table.headers if h.lower() not in {"", "total", "#", "nr", "nº"}]
                if col_names:
                    suggestions.append(f"Liste os registros ordenados por {col_names[-1]}.")
                if len(table.rows) > 5:
                    suggestions.append(f"Quais são os {min(5, len(table.rows))} maiores valores?")

        # Sugestões genéricas de complemento
        suggestions.extend(_DEFAULT_SUGGESTIONS[:max(0, 3 - len(suggestions))])

        return suggestions[:4]

    @staticmethod
    def _parse_numeric(value: str) -> Decimal | None:
        """Converte string monetária/numérica para Decimal."""
        raw = value.strip().lstrip("R$").strip()
        import re as _re
        m = _re.search(r"[\d.,]+", raw)
        if not m:
            return None
        s = m.group()
        if "," in s:
            s = s.replace(".", "").replace(",", ".")
        try:
            return Decimal(s)
        except InvalidOperation:
            return None
