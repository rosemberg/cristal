"""Extrator de relações entre documentos (Fase 6 NOVO_RAG).

Três estratégias implementadas via Template Method:
  A) entity   — páginas que compartilham a mesma entity_key em page_entities
  B) link     — links <a href> encontrados em main_content que apontam para
                outra página do portal (page_links, relação 'referencia')
  C) llm      — classificação assistida por LLM para pares com score de
                similaridade semântica alto (opcional / futura expansão)

O extrator de estratégia A (entity) é o mais completo e está implementado
inteiramente aqui.  As estratégias B e C são chamadas via métodos _abstract_
que as subclasses concretas (com acesso ao pool) devem implementar.
"""

from __future__ import annotations

import logging
import re
from abc import abstractmethod
from urllib.parse import urlparse

from app.domain.ports.outbound.relation_repository import RelationRepository
from app.domain.value_objects.document_relation import (
    DocumentRelation,
    RelationExtractionResult,
)

logger = logging.getLogger(__name__)

# ── Regex para detectar entidades chave que ligam documentos ─────────────────
# Usamos os mesmos padrões das entidades de Phase 3 como base

_RE_CONTRACT = re.compile(
    r"\b(?:contrato|conv[eê]nio|ajuste)\s+n[°º.]?\s*\d[\d./\-]+",
    re.IGNORECASE,
)
_RE_PREGAO = re.compile(
    r"\b(?:preg[aã]o|concorr[eê]ncia|tomada\s+de\s+pre[çc]os)\s+n[°º.]?\s*\d[\d./\-]+",
    re.IGNORECASE,
)
_RE_PROCESS = re.compile(
    r"\b(?:processo|proc\.?)\s+n[°º.]?\s*[\d./\-]{5,}",
    re.IGNORECASE,
)

_ENTITY_PATTERNS = [
    ("contract",  _RE_CONTRACT),
    ("pregao",    _RE_PREGAO),
    ("process",   _RE_PROCESS),
]

# Tipo de relação inferido a partir do tipo de entidade
_ENTITY_RELATION_MAP = {
    "contract": "origina",   # licitação origina contrato (ou o inverso: contrato decorre_de)
    "pregao":   "origina",
    "process":  "referencia",
}


class RelationExtractor:
    """Orquestra as três estratégias de extração de relações."""

    def __init__(self, repo: RelationRepository) -> None:
        self._repo = repo

    # ── API pública ──────────────────────────────────────────────────────────

    async def extract_entity_relations(
        self,
        batch_size: int = 200,
        skip_covered: bool = True,
    ) -> RelationExtractionResult:
        """Estratégia A: liga páginas pelo mesmo entity_key compartilhado."""
        result = RelationExtractionResult()
        covered = (
            await self._repo.get_covered_page_ids(strategy="entity")
            if skip_covered
            else set()
        )

        offset = 0
        while True:
            rows = await self._fetch_entity_groups(batch_size=batch_size, offset=offset)
            if not rows:
                break

            relations: list[DocumentRelation] = []
            for group in rows:
                page_id = group["page_id"]
                if page_id in covered:
                    result.skipped += 1
                    continue
                group_relations = self._build_entity_relations(group)
                relations.extend(group_relations)
                result.pages_processed += 1

            if relations:
                inserted = await self._repo.save_relations_batch(relations)
                result.relations_found += len(relations)
                result.relations_inserted += inserted
                result.strategy_counts["entity"] = (
                    result.strategy_counts.get("entity", 0) + inserted
                )
            logger.info(
                "Estratégia entity: %d relações (offset=%d)", len(relations), offset
            )
            offset += batch_size

        return result

    async def extract_link_relations(
        self,
        base_domain: str = "tre-pi.jus.br",
        batch_size: int = 100,
        skip_covered: bool = True,
    ) -> RelationExtractionResult:
        """Estratégia B: extrai relações a partir de links em main_content."""
        result = RelationExtractionResult()
        covered = (
            await self._repo.get_covered_page_ids(strategy="link")
            if skip_covered
            else set()
        )

        offset = 0
        while True:
            pages = await self._fetch_pages_with_content(batch_size=batch_size, offset=offset)
            if not pages:
                break

            relations: list[DocumentRelation] = []
            for page in pages:
                if page["id"] in covered:
                    result.skipped += 1
                    continue
                page_relations = await self._build_link_relations(
                    page, base_domain=base_domain
                )
                relations.extend(page_relations)
                result.pages_processed += 1

            if relations:
                inserted = await self._repo.save_relations_batch(relations)
                result.relations_found += len(relations)
                result.relations_inserted += inserted
                result.strategy_counts["link"] = (
                    result.strategy_counts.get("link", 0) + inserted
                )
            offset += batch_size

        return result

    async def get_status(self) -> dict[str, int]:
        return await self._repo.get_status()

    # ── Lógica domain: estratégia entity ────────────────────────────────────

    def _build_entity_relations(self, group: dict) -> list[DocumentRelation]:
        """Dado um grupo {page_id, entity_type, entity_value, peer_page_ids},
        cria relações 'referencia' entre a página e todos os pares."""
        relations = []
        source_id: int = group["page_id"]
        entity_key: str = group["entity_key"]
        entity_type: str = group["entity_type"]
        peers: list[int] = group["peer_page_ids"]

        relation_type = _ENTITY_RELATION_MAP.get(entity_type, "referencia")

        for peer_id in peers:
            if peer_id == source_id:
                continue
            try:
                rel = DocumentRelation(
                    source_page_id=source_id,
                    target_page_id=peer_id,
                    relation_type=relation_type,
                    strategy="entity",
                    entity_key=entity_key,
                    context=f"Entidade compartilhada: {entity_key}",
                    confidence=0.9,
                )
                relations.append(rel)
            except ValueError:
                pass
        return relations

    # ── Lógica domain: estratégia link ───────────────────────────────────────

    async def _build_link_relations(
        self, page: dict, base_domain: str
    ) -> list[DocumentRelation]:
        """Cria relações 'referencia' a partir da tabela page_links já populada
        pelo crawler. Evita re-parsear texto (main_content é texto puro, sem HTML).
        """
        source_id: int = page["id"]
        source_url: str = page["url"]

        link_rows = await self._fetch_page_links(source_url)
        relations: list[DocumentRelation] = []

        for row in link_rows:
            target_url: str = row["target_url"]
            target_id: int | None = row.get("target_page_id")
            link_title: str | None = row.get("link_title")

            # Ignora documentos (PDF, CSV, XLS) — são tratados em outra fase
            if any(target_url.lower().endswith(ext) for ext in (".pdf", ".csv", ".xls", ".xlsx")):
                continue

            # Só processa links internos ao domínio
            parsed = urlparse(target_url)
            if base_domain not in parsed.netloc and not target_url.startswith("/"):
                continue

            # Pula auto-referências
            if target_id == source_id:
                continue

            try:
                rel = DocumentRelation(
                    source_page_id=source_id,
                    target_page_id=target_id,
                    target_url=target_url if target_id is None else None,
                    relation_type="referencia",
                    strategy="link",
                    context=f"Link: {link_title or target_url[:120]}",
                    confidence=1.0,
                )
                relations.append(rel)
            except ValueError:
                pass

        return relations

    # ── Template methods — implementados no adapter ──────────────────────────

    @abstractmethod
    async def _fetch_entity_groups(
        self, batch_size: int, offset: int
    ) -> list[dict]:
        """Retorna grupos de páginas que compartilham o mesmo entity_key.

        Cada dict: {page_id, entity_type, entity_key, peer_page_ids: list[int]}
        """

    @abstractmethod
    async def _fetch_pages_with_content(
        self, batch_size: int, offset: int
    ) -> list[dict]:
        """Retorna páginas com main_content para extração de links.

        Cada dict: {id, url, main_content}
        """

    @abstractmethod
    async def _resolve_page_id_by_url(self, url: str) -> int | None:
        """Resolve URL para page_id via lookup no banco."""

    @abstractmethod
    async def _fetch_page_links(self, source_url: str) -> list[dict]:
        """Retorna os links extraídos por crawler para uma página.

        Cada dict: {target_url, target_page_id (int | None), link_title}.
        target_page_id é NULL se o destino não existe em pages.
        """
