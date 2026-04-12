"""Value objects para geração de perguntas sintéticas (Query Augmentation)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SyntheticQuery:
    """Representa uma pergunta sintética gerada para um chunk."""

    source_type: str    # 'page_chunk' | 'chunk' | 'table'
    source_id: int      # FK lógica para o chunk/tabela de origem
    question: str       # Pergunta gerada pelo LLM
    model_used: str     # Ex: 'gemini-2.5-flash-lite'
    id: int | None = None


@dataclass
class GenerationResult:
    """Resultado de uma execução do SyntheticQueryGeneratorService."""

    chunks_processed: int = 0
    questions_generated: int = 0
    embeddings_created: int = 0
    errors: int = 0
    skipped: int = 0    # chunks já com perguntas (não forçou regeneração)
