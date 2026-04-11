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
- Responda sempre em português do Brasil, de forma clara e objetiva.
- Base suas respostas exclusivamente nas informações do contexto fornecido.
- Se a informação não estiver disponível no contexto, informe isso honestamente.
- Forneça links diretos quando disponíveis.
- Cite as fontes de cada informação apresentada.

## Formato de resposta (JSON obrigatório)
Responda **sempre** no seguinte formato JSON:
```json
{
  "text": "Resposta principal em texto corrido.",
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
      "rows": [["valor 1", "valor 2"]],
      "source_document": "https://...",
      "title": "Título da tabela",
      "page_number": 1
    }
  ],
  "suggestions": ["Pergunta sugerida 1", "Pergunta sugerida 2"]
}
```

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
            parts.append("\n## Trechos de documentos")
            for cm in chunks:
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
                rows = "\n".join(" | ".join(row) for row in table.rows[:10])
                title = table.caption or "Tabela"
                parts.append(f"### {title}\n{header}\n{sep}\n{rows}")

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
