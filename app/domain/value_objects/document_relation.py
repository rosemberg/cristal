"""Value objects para relações entre documentos (Fase 6 NOVO_RAG)."""

from __future__ import annotations

from dataclasses import dataclass, field


_VALID_RELATION_TYPES = frozenset({
    "referencia",
    "atualiza",
    "substitui",
    "complementa",
    "origina",
    "decorre_de",
})

_VALID_STRATEGIES = frozenset({"entity", "link", "llm"})


@dataclass
class DocumentRelation:
    """Relação semântica entre duas páginas do portal."""

    source_page_id: int
    relation_type: str                  # um de _VALID_RELATION_TYPES
    strategy: str                       # 'entity' | 'link' | 'llm'
    target_page_id: int | None = None   # página destino (quando conhecida)
    target_url: str | None = None       # URL destino (quando page_id não resolvido)
    context: str | None = None          # trecho do texto que motivou a relação
    entity_key: str | None = None       # chave da entidade que ligou os docs
    confidence: float = 1.0
    id: int | None = None

    def __post_init__(self) -> None:
        if self.relation_type not in _VALID_RELATION_TYPES:
            raise ValueError(
                f"relation_type inválido: {self.relation_type!r}. "
                f"Válidos: {sorted(_VALID_RELATION_TYPES)}"
            )
        if self.strategy not in _VALID_STRATEGIES:
            raise ValueError(
                f"strategy inválida: {self.strategy!r}. "
                f"Válidas: {sorted(_VALID_STRATEGIES)}"
            )
        if self.target_page_id is None and self.target_url is None:
            raise ValueError("Informe target_page_id ou target_url.")


@dataclass
class RelationExtractionResult:
    """Relatório de uma execução do extrator de relações."""

    pages_processed: int = 0
    relations_found: int = 0
    relations_inserted: int = 0
    errors: int = 0
    skipped: int = 0
    strategy_counts: dict[str, int] = field(default_factory=dict)
