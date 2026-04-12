"""Domain service: MetadataEnricherService.

Enriquece páginas com metadados estruturados via duas etapas:

Etapa A — NER com regex (síncrono, sem LLM):
  - Datas, valores monetários, números de processo/contrato/pregão, CNPJs

Etapa B — Classificação temática via LLM (assíncrono, em batch):
  - Tags semânticas normalizadas (licitacao, contrato, diaria, etc.)
  - Período de referência opcional (salvo como entidade date_range)

Fallback:
- JSON malformado → tenta regex antes de descartar o batch
- Erros LLM → registra erro, continua para próximo batch
"""

from __future__ import annotations

import asyncio
import json
import logging
import re

from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.ports.outbound.metadata_repository import MetadataRepository
from app.domain.value_objects.enriched_metadata import (
    EnrichmentResult,
    PageEntity,
    PageTag,
)

logger = logging.getLogger(__name__)

# ─── Prompt (Etapa B) ─────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "Você é um especialista em transparência pública brasileira. "
    "Classifique páginas do portal de transparência do TRE-PI. "
    "Responda APENAS com JSON válido, sem texto adicional."
)

_VALID_TAGS = frozenset([
    "licitacao", "contrato", "convenio", "diaria", "passagem",
    "folha_pagamento", "relatorio_gestao", "orcamento", "receita",
    "despesa", "patrimonio", "prestacao_contas", "auditoria",
    "ata", "resolucao", "portaria",
])

_USER_PROMPT_TEMPLATE = """\
Para cada página abaixo, classifique em 1 a 3 categorias da lista e \
informe o período de referência, se houver.

Categorias válidas:
licitacao, contrato, convenio, diaria, passagem, folha_pagamento,
relatorio_gestao, orcamento, receita, despesa, patrimonio,
prestacao_contas, auditoria, ata, resolucao, portaria

Retorne JSON no formato:
[{{"page_id": N, "tags": ["tag1", "tag2"], "periodo_referencia": "2024"}}]

Use null em periodo_referencia quando não houver ano/período identificável.

Páginas:
{pages_json}
"""

# ─── Padrões regex (Etapa A) ──────────────────────────────────────────────────

_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Datas específicas (dd/mm/aaaa)
    ("date_range", re.compile(r"\b\d{2}/\d{2}/\d{4}\b")),
    # Exercício fiscal (ex: "exercício 2024")
    ("date_range", re.compile(r"\bexerc[íi]cio\s+\d{4}\b", re.IGNORECASE)),
    # Semestre (ex: "1º semestre de 2024")
    ("date_range", re.compile(r"\b\d{1,2}[°º]\s*semestre(?:\s+de)?\s+\d{4}\b", re.IGNORECASE)),
    # Valores monetários (ex: "R$ 1.234,56")
    ("monetary_value", re.compile(r"R\$\s?[\d.,]{3,}")),
    # Valores por extenso (ex: "500 mil", "2,5 milhões")
    ("monetary_value", re.compile(r"\b\d+[\d.,]*\s*(?:mil|milh[õo]es|bilh[õo]es)\b", re.IGNORECASE)),
    # Número de processo CNJ (ex: "0001234-56.2024.6.18.0000")
    ("process_number", re.compile(r"\b\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}\b")),
    # Número de contrato (ex: "Contrato nº 012/2024")
    ("contract_number", re.compile(r"\bContrato\s+n[°º]?\.?\s*[\d.]+/\d{4}\b", re.IGNORECASE)),
    # Pregão eletrônico/presencial (ex: "Pregão Eletrônico nº 005/2024")
    ("pregao", re.compile(
        r"\bPreg[ãa]o\s+(?:Eletr[ôo]nico|Presencial)\s+n[°º]?\.?\s*[\d.]+/\d{4}\b",
        re.IGNORECASE,
    )),
    # CNPJ (ex: "12.345.678/0001-90")
    ("cpf_cnpj", re.compile(r"\b\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}\b")),
]

# Regex fallback para parsear resposta do LLM
_JSON_ARRAY_RE = re.compile(
    r'\{[^{}]*"page_id"\s*:\s*(\d+)[^{}]*"tags"\s*:\s*(\[[^\]]*\])[^{}]*\}'
)


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _extract_entities_regex(page_id: int, text: str) -> list[PageEntity]:
    """Extrai entidades do texto usando regex. Deduplica por (type, value)."""
    seen: set[tuple[str, str]] = set()
    entities: list[PageEntity] = []

    for entity_type, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            raw = match.group(0)
            value = raw.strip().rstrip(".,;:")
            key = (entity_type, value.lower())
            if key in seen:
                continue
            seen.add(key)
            entities.append(
                PageEntity(
                    page_id=page_id,
                    entity_type=entity_type,
                    entity_value=value,
                    raw_text=raw,
                    confidence=1.0,
                )
            )

    return entities


def _parse_llm_response(raw: str) -> list[dict]:
    """Tenta parsear resposta JSON do LLM. Fallback para regex."""
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
        text = text.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed  # type: ignore[return-value]
    except json.JSONDecodeError:
        pass

    # Regex fallback
    results = []
    for m in _JSON_ARRAY_RE.finditer(raw):
        try:
            page_id = int(m.group(1))
            tags = json.loads(m.group(2))
            results.append({"page_id": page_id, "tags": tags, "periodo_referencia": None})
        except (ValueError, json.JSONDecodeError):
            continue
    return results


def _build_pages_json(pages: list[dict]) -> str:
    """Serializa lista de páginas para o prompt LLM."""
    items = []
    for p in pages:
        content_preview = (p.get("main_content") or "")[:2000]
        items.append({
            "page_id": p["id"],
            "title": p.get("title") or "",
            "category": p.get("category") or "",
            "subcategory": p.get("subcategory") or "",
            "content": content_preview,
        })
    return json.dumps(items, ensure_ascii=False, indent=2)


# ─── Service ──────────────────────────────────────────────────────────────────


class MetadataEnricherService:
    """Orquestra o enriquecimento de metadados estruturados para páginas."""

    def __init__(
        self,
        llm_gateway: LLMGateway,
        metadata_repo: MetadataRepository,
        model_name: str = "gemini-2.5-flash-lite",
        llm_batch_size: int = 10,
    ) -> None:
        self._llm = llm_gateway
        self._repo = metadata_repo
        self._model_name = model_name
        self._llm_batch_size = llm_batch_size

    # ── enrich_pending_pages (Etapa A + B) ───────────────────────────────────

    async def enrich_pending_pages(self, batch_size: int = 50) -> EnrichmentResult:
        """Executa Etapa A (regex) + Etapa B (LLM) para páginas sem tags."""
        result = EnrichmentResult()

        while True:
            covered = await self._repo.get_covered_page_ids()
            pages = await self._fetch_pending_pages_from_db(covered, batch_size)
            if not pages:
                result.skipped = len(covered)
                break

            sub = await self._process_batch(pages, run_regex=True, run_llm=True)
            result.pages_processed += sub.pages_processed
            result.entities_extracted += sub.entities_extracted
            result.tags_extracted += sub.tags_extracted
            result.errors += sub.errors

        return result

    # ── enrich_all_pages_regex (apenas Etapa A) ───────────────────────────────

    async def enrich_all_pages_regex(self, batch_size: int = 200) -> EnrichmentResult:
        """Executa apenas regex em todas as páginas (sem LLM, sem rastreamento de cobertura)."""
        result = EnrichmentResult()
        offset = 0

        while True:
            pages = await self._fetch_pages_paginated(offset, batch_size)
            if not pages:
                break

            for page in pages:
                try:
                    entities = _extract_entities_regex(
                        page["id"], page.get("main_content") or ""
                    )
                    await self._repo.delete_entities_by_page(page["id"])
                    if entities:
                        await self._repo.save_entities_batch(entities)
                    result.entities_extracted += len(entities)
                    result.pages_processed += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "MetadataEnricher: erro regex página %d: %s", page["id"], exc
                    )
                    result.errors += 1

            offset += batch_size

        return result

    # ── enrich_pending_pages_llm (apenas Etapa B) ────────────────────────────

    async def enrich_pending_pages_llm(self, batch_size: int = 50) -> EnrichmentResult:
        """Executa apenas Etapa B (LLM) para páginas sem tags."""
        result = EnrichmentResult()

        while True:
            covered = await self._repo.get_covered_page_ids()
            pages = await self._fetch_pending_pages_from_db(covered, batch_size)
            if not pages:
                result.skipped = len(covered)
                break

            sub = await self._process_batch(pages, run_regex=False, run_llm=True)
            result.pages_processed += sub.pages_processed
            result.entities_extracted += sub.entities_extracted
            result.tags_extracted += sub.tags_extracted
            result.errors += sub.errors

        return result

    # ── reenrich_page ─────────────────────────────────────────────────────────

    async def reenrich_page(self, page_id: int) -> tuple[int, int]:
        """Remove e regenera entidades e tags para uma página. Retorna (entities, tags)."""
        page = await self._fetch_single_page_from_db(page_id)
        if page is None:
            logger.warning("MetadataEnricher: página %d não encontrada", page_id)
            return 0, 0

        await self._repo.delete_by_page(page_id)

        sub = await self._process_batch([page], run_regex=True, run_llm=True)
        return sub.entities_extracted, sub.tags_extracted

    # ── get_status ────────────────────────────────────────────────────────────

    async def get_status(self) -> dict[str, object]:
        """Retorna status atual do enriquecimento."""
        repo_stats = await self._repo.get_status()
        total_pages = await self._count_total_pages()
        covered = await self._repo.get_covered_page_ids()

        pages_done = len(covered)
        return {
            "pages_with_tags": pages_done,
            "total_pages": total_pages,
            "pages_pending": max(0, total_pages - pages_done),
            **repo_stats,
        }

    # ── Helpers internos ──────────────────────────────────────────────────────

    async def _process_batch(
        self,
        pages: list[dict],
        *,
        run_regex: bool,
        run_llm: bool,
    ) -> EnrichmentResult:
        """Processa um batch de páginas: regex + LLM → save."""
        result = EnrichmentResult()

        # Etapa A: regex (por página, síncrono)
        if run_regex:
            for page in pages:
                try:
                    entities = _extract_entities_regex(
                        page["id"], page.get("main_content") or ""
                    )
                    if entities:
                        await self._repo.save_entities_batch(entities)
                    result.entities_extracted += len(entities)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "MetadataEnricher: erro regex página %d: %s", page["id"], exc
                    )

        # Etapa B: LLM classification (em sub-batches)
        if run_llm:
            for i in range(0, len(pages), self._llm_batch_size):
                sub_pages = pages[i : i + self._llm_batch_size]
                try:
                    tags_data = await self._call_llm(sub_pages)
                    await self._save_tags_and_period_entities(tags_data)
                    result.tags_extracted += sum(
                        len(d.get("tags", [])) for d in tags_data.values()
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "MetadataEnricher: erro LLM batch [%d:%d]: %s",
                        i, i + len(sub_pages), exc,
                    )
                    result.errors += len(sub_pages)
                finally:
                    # Pausa entre sub-batches para evitar quota 429
                    if i + self._llm_batch_size < len(pages):
                        await asyncio.sleep(15)

        result.pages_processed += len(pages)
        return result

    async def _call_llm(self, pages: list[dict]) -> dict[int, dict]:
        """Chama LLM e retorna {page_id: {tags, periodo_referencia}}."""
        prompt = _USER_PROMPT_TEMPLATE.format(pages_json=_build_pages_json(pages))
        raw = await self._llm.generate(
            system_prompt=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,  # baixa temperatura para classificação consistente
        )
        parsed = _parse_llm_response(raw)

        result: dict[int, dict] = {}
        for item in parsed:
            page_id = item.get("page_id")
            if not isinstance(page_id, int):
                continue
            tags = [
                t for t in item.get("tags", [])
                if isinstance(t, str) and t in _VALID_TAGS
            ]
            periodo = item.get("periodo_referencia")
            if tags:  # só persiste se houver pelo menos uma tag válida
                result[page_id] = {
                    "tags": tags,
                    "periodo_referencia": str(periodo) if periodo else None,
                }
        return result

    async def _save_tags_and_period_entities(
        self, tags_data: dict[int, dict]
    ) -> None:
        """Persiste tags e entidades de período para cada página."""
        all_tags: list[PageTag] = []
        period_entities: list[PageEntity] = []

        for page_id, data in tags_data.items():
            for tag in data.get("tags", []):
                all_tags.append(PageTag(page_id=page_id, tag=tag, confidence=0.9))

            periodo = data.get("periodo_referencia")
            if periodo:
                period_entities.append(
                    PageEntity(
                        page_id=page_id,
                        entity_type="date_range",
                        entity_value=periodo,
                        confidence=0.8,
                    )
                )

        if all_tags:
            await self._repo.save_tags_batch(all_tags)
        if period_entities:
            await self._repo.save_entities_batch(period_entities)

    # ── Template methods (implementados pelo adapter Postgres) ────────────────

    async def _fetch_pending_pages_from_db(
        self, covered: set[int], limit: int
    ) -> list[dict]:
        """Busca páginas sem tags no banco."""
        raise NotImplementedError(
            "MetadataEnricherService._fetch_pending_pages_from_db deve ser "
            "implementado pelo adapter concreto (PostgresMetadataEnricherService)."
        )

    async def _fetch_pages_paginated(self, offset: int, limit: int) -> list[dict]:
        """Busca páginas em ordem por ID com offset/limit (para regex-all)."""
        raise NotImplementedError(
            "MetadataEnricherService._fetch_pages_paginated deve ser "
            "implementado pelo adapter concreto."
        )

    async def _fetch_single_page_from_db(self, page_id: int) -> dict | None:
        """Busca uma única página pelo ID."""
        raise NotImplementedError(
            "MetadataEnricherService._fetch_single_page_from_db deve ser "
            "implementado pelo adapter concreto."
        )

    async def _count_total_pages(self) -> int:
        """Retorna total de páginas com conteúdo."""
        raise NotImplementedError(
            "MetadataEnricherService._count_total_pages deve ser "
            "implementado pelo adapter concreto."
        )
