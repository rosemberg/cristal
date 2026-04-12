"""Serviço de scoring de qualidade de chunks (Fase 5 NOVO_RAG).

Critérios e pesos:
  1. comprimento_adequado    0.15  — entre MIN e MAX tokens
  2. densidade_palavras      0.25  — palavras reais / total de tokens
  3. ausencia_ocr_artefatos  0.20  — baixa proporção de símbolos/lixo OCR
  4. coerencia_sentencas     0.15  — proporção de frases terminando corretamente
  5. ausencia_dup_interna    0.10  — sem repetição de frases dentro do chunk
  6. conteudo_informativo    0.15  — densidade de n-gramas únicos

Flags emitidas:
  too_short, too_long, low_density, ocr_artifacts, incoherent,
  internal_dup, low_information, duplicate
"""

from __future__ import annotations

import hashlib
import logging
import re
from abc import abstractmethod
from collections import Counter

from app.domain.ports.outbound.chunk_quality_repository import ChunkQualityRepository
from app.domain.value_objects.chunk_quality import ChunkQualityResult, QualityReport

logger = logging.getLogger(__name__)

# ── Limites de comprimento ───────────────────────────────────────────────────
_MIN_TOKENS = 30
_MAX_TOKENS = 1200
_TOKENS_PER_WORD = 1.3

# ── Pesos dos critérios ──────────────────────────────────────────────────────
_WEIGHTS: dict[str, float] = {
    "comprimento_adequado": 0.15,
    "densidade_palavras":   0.25,
    "ausencia_ocr":         0.20,
    "coerencia_sentencas":  0.15,
    "ausencia_dup_interna": 0.10,
    "conteudo_informativo": 0.15,
}

# ── Regex auxiliares ─────────────────────────────────────────────────────────
_RE_WORD    = re.compile(r"\b[a-záéíóúãõâêîôûàç]{3,}\b", re.IGNORECASE)
_RE_JUNK    = re.compile(r"[^\w\s.,;:()\-/]", re.UNICODE)          # caracteres "lixo"
_RE_SENT_END = re.compile(r"[.!?]\s")
_RE_REPEATED_LINE = re.compile(r"(.{20,})\n\1", re.MULTILINE)      # linhas duplicadas

# tabela: ≥2 células pipe-separated
_RE_TABLE_LINE = re.compile(r"\|[^|]+\|")


def _est_tokens(text: str) -> int:
    return int(len(text.split()) * _TOKENS_PER_WORD)


class ChunkQualityScorer:
    """Avalia chunks e persiste os resultados via ChunkQualityRepository."""

    def __init__(self, repo: ChunkQualityRepository) -> None:
        self._repo = repo

    # ── API pública ──────────────────────────────────────────────────────────

    async def score_pending(
        self,
        table: str,
        batch_size: int = 500,
    ) -> QualityReport:
        """Pontua todos os chunks sem quality_score na tabela indicada."""
        report = QualityReport()
        offset = 0
        while True:
            rows = await self._repo.fetch_unscored_chunks(table, limit=batch_size, offset=offset)
            if not rows:
                break
            results = [self._score_chunk(r["id"], table, r["chunk_text"]) for r in rows]
            await self._repo.save_quality_batch(results)
            report.chunks_scored += len(results)
            report.chunks_quarantined += sum(1 for r in results if r.quarantined)
            self._accumulate_flags(report, results)
            self._accumulate_distribution(report, results)
            logger.info("Pontuados %d chunks de %s (offset=%d)", len(results), table, offset)
            offset += batch_size
        return report

    async def deduplicate(self, table: str, batch_size: int = 5000) -> QualityReport:
        """Detecta e marca duplicatas por hash SHA-256 do texto normalizado."""
        report = QualityReport()
        seen: dict[str, int] = {}           # hash → first_id
        duplicate_ids: list[int] = []

        offset = 0
        while True:
            rows = await self._repo.fetch_all_texts(table, limit=batch_size, offset=offset)
            if not rows:
                break
            for r in rows:
                h = _text_hash(r["chunk_text"])
                if h in seen:
                    duplicate_ids.append(r["id"])
                else:
                    seen[h] = r["id"]
            offset += batch_size

        if duplicate_ids:
            report.chunks_deduplicated = await self._repo.mark_duplicates_quarantined(
                table, duplicate_ids
            )
        return report

    async def get_report(self) -> QualityReport:
        return await self._repo.get_report()

    # ── Scoring de um único chunk ────────────────────────────────────────────

    def _score_chunk(self, chunk_id: int, table: str, text: str) -> ChunkQualityResult:
        scores: dict[str, float] = {}
        flags: list[str] = []

        text = text or ""
        tokens = _est_tokens(text)

        # 1. Comprimento
        if tokens < _MIN_TOKENS:
            scores["comprimento_adequado"] = 0.0
            flags.append("too_short")
        elif tokens > _MAX_TOKENS:
            scores["comprimento_adequado"] = 0.5
            flags.append("too_long")
        else:
            scores["comprimento_adequado"] = 1.0

        # 2. Densidade de palavras
        words = _RE_WORD.findall(text)
        total_tokens = max(tokens, 1)
        density = len(words) / total_tokens
        scores["densidade_palavras"] = min(density / 0.6, 1.0)   # 0.6 = referência boa
        if density < 0.3:
            flags.append("low_density")

        # 3. Ausência de artefatos OCR
        junk_chars = len(_RE_JUNK.findall(text))
        junk_ratio = junk_chars / max(len(text), 1)
        scores["ausencia_ocr"] = max(1.0 - junk_ratio * 10, 0.0)
        if junk_ratio > 0.05:
            flags.append("ocr_artifacts")

        # 4. Coerência de sentenças
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        good = sum(1 for s in sentences if len(s.split()) >= 3)
        coherence = good / max(len(sentences), 1)
        scores["coerencia_sentencas"] = coherence
        if coherence < 0.4:
            flags.append("incoherent")

        # 5. Ausência de duplicação interna
        if _RE_REPEATED_LINE.search(text):
            scores["ausencia_dup_interna"] = 0.3
            flags.append("internal_dup")
        else:
            # Heurística: proporção de bi-gramas únicos
            toks = text.split()
            bigrams = [f"{a} {b}" for a, b in zip(toks, toks[1:])]
            if bigrams:
                uniq_ratio = len(set(bigrams)) / len(bigrams)
                scores["ausencia_dup_interna"] = uniq_ratio
                if uniq_ratio < 0.5:
                    flags.append("internal_dup")
            else:
                scores["ausencia_dup_interna"] = 1.0

        # 6. Conteúdo informativo (riqueza de trigramas únicos)
        toks = text.split()
        trigrams = [f"{a} {b} {c}" for a, b, c in zip(toks, toks[1:], toks[2:])]
        if trigrams:
            info_score = min(len(set(trigrams)) / max(len(trigrams), 1) / 0.8, 1.0)
        else:
            info_score = 0.0
        scores["conteudo_informativo"] = info_score
        if info_score < 0.3:
            flags.append("low_information")

        # Média ponderada
        final = sum(_WEIGHTS[k] * v for k, v in scores.items()) / sum(_WEIGHTS.values())
        quarantined = final < 0.5

        return ChunkQualityResult(
            chunk_id=chunk_id,
            source_table=table,
            score=round(final, 4),
            flags=flags,
            quarantined=quarantined,
        )

    # ── Helpers de relatório ─────────────────────────────────────────────────

    @staticmethod
    def _accumulate_flags(report: QualityReport, results: list[ChunkQualityResult]) -> None:
        for r in results:
            for flag in r.flags:
                report.flag_counts[flag] = report.flag_counts.get(flag, 0) + 1

    @staticmethod
    def _accumulate_distribution(
        report: QualityReport, results: list[ChunkQualityResult]
    ) -> None:
        for r in results:
            bucket = f"{int(r.score * 10) / 10:.1f}"
            report.score_distribution[bucket] = (
                report.score_distribution.get(bucket, 0) + 1
            )


# ── Funções auxiliares ───────────────────────────────────────────────────────

def _text_hash(text: str) -> str:
    normalized = " ".join(text.lower().split())
    return hashlib.sha256(normalized.encode()).hexdigest()
