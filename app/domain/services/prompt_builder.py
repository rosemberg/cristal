"""Domain service: PromptBuilder — pure, zero I/O."""

from __future__ import annotations

from app.domain.entities.document_table import DocumentTable
from app.domain.value_objects.intent import QueryIntent
from app.domain.value_objects.search_result import ChunkMatch, PageMatch

# ---------------------------------------------------------------------------
# Keyword maps for intent classification
# ---------------------------------------------------------------------------

_DOCUMENT_KEYWORDS = {
    "pdf", "documento", "arquivo", "baixar", "download", "anexo",
    "relatório", "planilha", "xlsx", "csv",
}

_DATA_KEYWORDS = {
    "quantos", "quanto", "total", "valor", "média", "porcentagem",
    "percentual", "número", "dado", "dados", "tabela", "lista",
    "servidores", "funcionários", "contratos",
}

_NAVIGATION_KEYWORDS = {
    "onde", "como chego", "como acesso", "link", "endereço",
    "seção", "aba", "menu", "página", "portal",
}

_FOLLOWUP_KEYWORDS = {
    "isso", "aquilo", "disse", "mencionou", "falou", "anterior",
    "acima", "antes", "continue",
}

# ---------------------------------------------------------------------------
# System prompt template
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
Você é o Cristal, assistente virtual de transparência do TRE-PI (Tribunal Regional Eleitoral do Piauí).

Seu objetivo é ajudar cidadãos a encontrar informações públicas no portal de transparência do TRE-PI,
respondendo perguntas sobre licitações, contratos, servidores, orçamento, eleições e demais dados públicos.

## Diretrizes
- Responda sempre em português do Brasil, de forma clara, amigável e objetiva.
- Base suas respostas exclusivamente nas informações do contexto fornecido.
- **APRESENTE os dados diretamente na resposta.** Nunca diga apenas "consulte o arquivo" ou "os dados podem ser encontrados no link". Extraia e mostre os valores, nomes, datas e números que estão no contexto.
- Quando houver dados tabulares (tabelas, planilhas, CSVs), organize-os em tabela estruturada no campo **"tables"** do JSON. Adicione uma última linha com totais quando houver colunas numéricas.
- **JAMAIS coloque conteúdo bruto de CSV, trechos com ponto-e-vírgula (;;;) ou pipe (|||) no campo "text".** O campo "text" deve conter apenas texto legível em Markdown. Dados tabulares vão exclusivamente em "tables".
- Se houver muitos registros, apresente todos os disponíveis no contexto (até 30 linhas). Informe o total se houver mais.
- **Use os dados EXATAMENTE como fornecidos no contexto.** Não invente, não reordene nem modifique nomes, cargos, valores ou datas.
- **SEMPRE cite a fonte** de cada informação com o link completo no campo "sources".
- Se a informação não estiver disponível no contexto, informe isso honestamente.
- Sugira perguntas relacionadas que ajudem o cidadão a explorar mais dados.

## Formatação do campo "text"
Use Markdown rico no campo "text":
- **Negrito** para valores-chave, nomes e totais (ex: **R$ 1.234.567,89**).
- `### Título` para seções dentro da resposta (ex: `### Maiores contratos`).
- Listas numeradas `1.` para itens ordenados por relevância ou valor.
- Listas com `-` para enumerações simples.
- Inclua um resumo analítico claro: total de registros, soma de valores, período coberto.

## Campo "metrics" (quando houver KPIs relevantes)
Quando a pergunta envolver quantidades, valores monetários ou totais, preencha o campo "metrics" com
os indicadores mais importantes extraídos dos dados. Exemplos:
- Total de registros encontrados
- Valor total (soma)
- Período coberto
- Maior/menor valor individual

## Formato de resposta (JSON obrigatório)
Responda **sempre** no seguinte formato JSON:
```json
{
  "text": "### Resumo\\nTexto com **valores em negrito** e listas quando necessário.",
  "metrics": [
    {"label": "Total de registros", "value": "1.234"},
    {"label": "Valor total", "value": "R$ 456.789,00"},
    {"label": "Período", "value": "Jan–Dez 2024"}
  ],
  "sources": [
    {
      "document_title": "Título do documento",
      "document_url": "https://...",
      "snippet": "Trecho relevante do documento",
      "page_number": 1
    }
  ],
  "tables": [
    {
      "headers": ["Coluna A", "Coluna B"],
      "rows": [["valor 1", "valor 2"], ["TOTAL", "soma"]],
      "source_document": "https://...",
      "title": "Título descritivo da tabela",
      "page_number": 1
    }
  ],
  "suggestions": ["Pergunta sugerida 1", "Pergunta sugerida 2"]
}
```

Omita o campo "metrics" (ou deixe `[]`) quando não houver KPIs relevantes.
Mantenha "sources" e "tables" vazios (`[]`) quando não houver dados relevantes.
"""


class PromptBuilder:
    """Builds prompts and classifies user intent. No external I/O."""

    def build_system_prompt(self) -> str:
        return _SYSTEM_PROMPT

    def build_context(
        self,
        pages: list[PageMatch],
        chunks: list[ChunkMatch],
        tables: list[DocumentTable],
    ) -> str:
        parts: list[str] = []

        if pages:
            parts.append("## Páginas encontradas")
            for match in pages:
                p = match.page
                line = f"- [{p.title}]({p.url})"
                if p.description:
                    line += f": {p.description}"
                parts.append(line)

        if chunks:
            # Exclude chunks from documents that already have structured tables —
            # sending the same data twice (raw text + structured) confuses the LLM.
            # Tables are pre-validated by TableValidatorAgent before reaching here.
            table_urls = {t.document_url for t in tables}
            filtered_chunks = [cm for cm in chunks if cm.document_url not in table_urls]
            if filtered_chunks:
                parts.append("\n## Trechos de documentos")
                for cm in filtered_chunks:
                    parts.append(
                        f"### {cm.document_title}\n"
                        f"Fonte: {cm.document_url}\n"
                        f"{cm.chunk.text}"
                    )

        if tables:
            parts.append("\n## Tabelas encontradas")
            for table in tables:
                header = " | ".join(table.headers)
                sep = " | ".join("---" for _ in table.headers)
                rows = "\n".join(" | ".join(row) for row in table.rows[:30])
                title = table.caption or "Tabela"
                parts.append(
                    f"### {title}\n"
                    f"Fonte: {table.document_url}\n"
                    f"{header}\n{sep}\n{rows}"
                )

        return "\n".join(parts)

    def classify_intent(self, query: str) -> QueryIntent:
        words = set(query.lower().split())

        if words & _FOLLOWUP_KEYWORDS:
            return QueryIntent.FOLLOWUP
        if words & _DOCUMENT_KEYWORDS:
            return QueryIntent.DOCUMENT_QUERY
        if words & _DATA_KEYWORDS:
            return QueryIntent.DATA_QUERY
        if words & _NAVIGATION_KEYWORDS:
            return QueryIntent.NAVIGATION
        return QueryIntent.GENERAL_SEARCH

    def format_history(
        self, history: list[dict[str, object]] | None
    ) -> list[dict[str, object]]:
        if not history:
            return []
        return [{"role": h["role"], "content": h["content"]} for h in history]
