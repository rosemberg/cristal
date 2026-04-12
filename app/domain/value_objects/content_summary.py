"""Value objects para sumarização de conteúdo (Fase 2 NOVO_RAG)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PageSummary:
    """Sumário de uma página gerado por LLM."""

    page_id: int         # FK lógica para pages.id
    summary_text: str    # 2-3 frases objetivas geradas pelo LLM
    model_used: str      # Ex: 'gemini-2.5-flash-lite'
    id: int | None = None


@dataclass
class SectionSummary:
    """Sumário de uma seção lógica de um documento longo (>10 páginas)."""

    document_id: int       # FK para document_contents.id
    summary_text: str      # Sumário da seção
    model_used: str        # Ex: 'gemini-2.5-flash-lite'
    section_title: str | None = None   # Título da seção (ex: "Receitas")
    page_range: str | None = None      # Intervalo de páginas (ex: "1-5")
    id: int | None = None


@dataclass
class SummarizationResult:
    """Resultado de uma execução do ContentSummarizerService."""

    pages_processed: int = 0
    summaries_generated: int = 0
    embeddings_created: int = 0
    errors: int = 0
    skipped: int = 0    # páginas já com sumário LLM (não forçou regeneração)
