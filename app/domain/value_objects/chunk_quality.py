"""Value objects para qualidade de chunks (Fase 5 NOVO_RAG)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ChunkQualityResult:
    """Resultado do scoring de qualidade de um chunk."""

    chunk_id: int
    source_table: str       # 'document_chunks' | 'page_chunks'
    score: float            # 0.0–1.0 (média ponderada dos critérios)
    flags: list[str]        # ex: ['ocr_artifacts', 'low_density']
    quarantined: bool = False

    @property
    def passes(self) -> bool:
        return self.score >= 0.5


@dataclass
class QualityReport:
    """Relatório de uma execução do pipeline de qualidade."""

    chunks_scored: int = 0
    chunks_quarantined: int = 0
    chunks_deduplicated: int = 0
    tables_normalized: int = 0
    errors: int = 0

    # Distribuição de scores (bucket de 0.1)
    score_distribution: dict[str, int] = field(default_factory=dict)
    flag_counts: dict[str, int] = field(default_factory=dict)
