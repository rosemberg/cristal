"""Domain service: PromptBuilder — pure, zero I/O."""

from __future__ import annotations

import re

from app.domain.entities.document_table import DocumentTable
from app.domain.value_objects.intent import QueryIntent
from app.domain.value_objects.search_result import ChunkMatch, PageMatch

# Máximo de chars de main_content por página enviados ao LLM (após normalização de espaços)
# 40.000 chars ≈ 10.000 tokens — cobre listas longas (ex: 48 de 52 contratos)
_MAX_PAGE_CONTENT_CHARS = 40_000

# Regex para normalizar whitespace excessivo gerado pelo crawler HTML
_MULTI_SPACE_RE = re.compile(r'[ \t]{2,}')
_MULTI_NEWLINE_RE = re.compile(r'\n{3,}')


def _normalize_whitespace(content: str) -> str:
    """Colapsa espaços/tabs múltiplos e linhas em branco excessivas.

    Páginas crawleadas de HTML frequentemente têm 90-96% de espaços em branco
    (cada célula de tabela/div gera dezenas de espaços de padding), o que faz
    o orçamento de 40k chars cobrir apenas 2-3 registros reais. Após normalizar,
    o mesmo orçamento cobre dezenas de registros.
    """
    content = _MULTI_SPACE_RE.sub(' ', content)
    content = _MULTI_NEWLINE_RE.sub('\n\n', content)
    return content


def _extract_relevant_section(content: str, query: str) -> str:
    """Extrai a seção do main_content mais relevante para a query.

    1. Normaliza espaços excessivos (páginas crawleadas têm 90-96% whitespace).
    2. Para queries com ano (ex: "contratos de 2025"), localiza o primeiro
       registro daquele ano no conteúdo e inicia o extrato a partir daí,
       evitando capturar dados de anos adjacentes.
    """
    if not content:
        return ""

    # Normaliza primeiro — busca posicional opera no texto limpo
    content = _normalize_whitespace(content)

    stopwords = {"de", "do", "da", "dos", "das", "em", "no", "na", "os", "as",
                 "e", "o", "a", "um", "uma", "para", "por", "com", "se", "que",
                 "liste", "listar", "mostre", "quais", "qual", "como"}
    terms = [
        w.lower() for w in re.findall(r'\w+', query)
        if len(w) > 3 and w.lower() not in stopwords
    ]

    best_pos = 0
    for term in terms:
        if re.match(r'^20\d{2}$', term):
            # Ano de 4 dígitos: localiza o primeiro registro com "XX/{ano}"
            # (ex: "Contrato 52/2025" ou "Processo 0001-01/2025")
            year_record = re.search(
                rf'\b\d+/{re.escape(term)}\b', content
            )
            if year_record:
                # Recua até o início da linha/registro para não cortar no meio
                line_start = content.rfind('\n', 0, year_record.start())
                best_pos = max(0, line_start)
                break
            # Fallback: header de seção isolado na linha
            year_header = re.search(
                rf'(?:^|\n)\s*{re.escape(term)}\s*\n', content
            )
            if year_header:
                best_pos = year_header.start()
                break
        else:
            idx = content.lower().find(term)
            if idx != -1:
                best_pos = max(best_pos, idx)

    end = min(len(content), best_pos + _MAX_PAGE_CONTENT_CHARS)
    return content[best_pos:end]

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
- Quando houver dados tabulares (tabelas, planilhas, CSVs), organize-os em tabela estruturada no campo **"tables"** do JSON.
- **JAMAIS coloque conteúdo bruto de CSV, trechos com ponto-e-vírgula (;;;) ou pipe (|||) no campo "text".** O campo "text" deve conter apenas texto legível em Markdown. Dados tabulares vão exclusivamente em "tables".
- Se houver muitos registros, apresente todos os disponíveis no contexto (até 30 linhas). Informe o total se houver mais.
- **Use os dados EXATAMENTE como fornecidos no contexto.** Não invente, não reordene nem modifique nomes, cargos, valores ou datas.
- **SEMPRE cite a fonte** de cada informação com o link completo no campo "sources".
- Se a informação não estiver disponível no contexto, informe isso honestamente.
- Sugira perguntas relacionadas que ajudem o cidadão a explorar mais dados.
- **Contratos** têm numeração no formato "Nº XX/AAAA" (ex: Contrato 20/2025). **Empenhos** têm formato "ANONExxxxx" (ex: 2025NE00236). São instrumentos distintos — ao listar contratos, não inclua empenhos, e vice-versa.

## REGRAS CRÍTICAS — redundância e totais

**PROIBIDO repetir dados da tabela no texto.**
Quando a resposta incluir o campo "tables", o campo "text" deve conter APENAS um resumo analítico
curto — por exemplo: "Foram encontradas 9 diárias em junho/2023, totalizando **R$ 14.459,02**."
NÃO liste nomes de servidores, valores individuais, destinos ou descrições no "text".
Todos os dados detalhados ficam EXCLUSIVAMENTE na tabela.

**Apenas UMA linha de total por tabela.**
Quando houver colunas numéricas, adicione exatamente UMA linha final com o rótulo "TOTAL" contendo
a soma dos valores. NÃO adicione linhas extras de subtotal, contagem ou qualquer outro totalizador.
A soma deve ser calculada com precisão aritmética — some cada valor da coluna e apresente o resultado exato.

## Formatação do campo "text"
Use Markdown rico no campo "text":
- **Negrito** para valores-chave, nomes e totais (ex: **R$ 1.234.567,89**).
- `### Título` para seções dentro da resposta (ex: `### Maiores contratos`).
- Listas numeradas `1.` para itens ordenados por relevância ou valor.
- Listas com `-` para enumerações simples.
- **Quando houver "tables" preenchido:** o "text" deve ter NO MÁXIMO 2-3 frases de resumo analítico (quantidade de registros, valor total, período). Exemplo correto: "Em junho de 2023, foram pagas **9 diárias** a servidores do TRE-PI, totalizando **R$ 14.459,02**. Os pagamentos referem-se a participações em eventos, seminários e verificações técnicas em diversas cidades." NÃO detalhe cada registro.

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
  "text": "Em junho de 2023, foram pagas **9 diárias** a servidores do TRE-PI, totalizando **R$ 14.459,02**. Os pagamentos referem-se a participações em eventos e verificações técnicas.",
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
      "rows": [["valor 1", "valor 2"], ["valor 3", "valor 4"], ["TOTAL", "soma exata"]],
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
        query: str = "",
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
                # Inclui extrato relevante do main_content quando disponível
                if p.main_content and len(p.main_content) > 500:
                    excerpt = _extract_relevant_section(p.main_content, query)
                    if excerpt.strip():
                        parts.append(
                            f"\n  **Conteúdo da página ({p.title}):**\n"
                            f"  Fonte: {p.url}\n"
                            + "\n".join(f"  {line}" for line in excerpt.splitlines())
                        )

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
