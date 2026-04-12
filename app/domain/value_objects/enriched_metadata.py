"""Value objects para enriquecimento de metadados estruturados (Fase 3 NOVO_RAG)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PageEntity:
    """Entidade estruturada extraída de uma página (NER regex ou LLM)."""

    page_id: int
    entity_type: str    # 'date_range' | 'monetary_value' | 'process_number' |
                        # 'contract_number' | 'pregao' | 'cpf_cnpj'
    entity_value: str   # Valor normalizado/original
    raw_text: str | None = None   # Texto original no documento
    confidence: float = 1.0       # 1.0 para regex; < 1.0 para LLM
    id: int | None = None


@dataclass
class PageTag:
    """Tag temática atribuída a uma página por classificação LLM."""

    page_id: int
    tag: str            # Ex: 'licitacao', 'contrato', 'diaria', 'folha_pagamento'
    confidence: float = 1.0
    id: int | None = None


@dataclass
class EnrichmentResult:
    """Resultado de uma execução do MetadataEnricherService."""

    pages_processed: int = 0
    entities_extracted: int = 0
    tags_extracted: int = 0
    errors: int = 0
    skipped: int = 0    # páginas já enriquecidas (não forçou reenrichment)
