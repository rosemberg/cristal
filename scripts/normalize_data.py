"""Script de normalização de dados — Etapa 8 Pipeline V2.

Ações:
1. Remove páginas sem categoria e sem conteúdo (lixo do crawling)
   — Registra cada uma como inconsistência 'empty_content' (detected_by='crawler')
     antes de remover.
2. Normaliza categorias duplicadas/mal-formatadas para nomes canônicos.
3. Registra categorias desconhecidas (não mapeadas) como 'category_mismatch'
   para revisão manual.

Uso:
    python -m scripts.normalize_data [--dry-run] [--database-url URL]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import asyncpg

# Adiciona a raiz do projeto ao PYTHONPATH quando executado como script
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from app.config.settings import Settings  # noqa: E402 (import após path setup)

logger = logging.getLogger(__name__)

# ─── Mapeamento de categorias ──────────────────────────────────────────────────

# Categorias antigas (mal-formatadas / duplicadas) → nome canônico
CATEGORY_MAP: dict[str, str] = {
    "Orcamento E Despesas": "Gestão Orçamentária e Financeira",
    "Despesas": "Gestão Orçamentária e Financeira",
    "Receitas": "Gestão Orçamentária e Financeira",
    "Tecnologia Da Informacao E Comunicacao": "Tecnologia da Informação",
    "Contratos": "Licitações, Contratos e Instrumentos de Cooperação",
    "Convenios": "Licitações, Contratos e Instrumentos de Cooperação",
    "Instrumentos De Cooperacao": "Licitações, Contratos e Instrumentos de Cooperação",
    "Gestao De Pessoas": "Gestão de Pessoas",
    "Servidores": "Gestão de Pessoas",
    "Estagiarios": "Gestão de Pessoas",
    "Planejamento E Gestao": "Planejamento e Gestão",
    "Prestacao De Contas": "Prestação de Contas",
    "Auditorias": "Auditoria e Correição",
    "Corregedoria": "Auditoria e Correição",
    "Institucional": "Informações Institucionais",
    "Composicao": "Informações Institucionais",
    "Legislacao": "Legislação e Normas",
    "Resolucoes": "Legislação e Normas",
    "Eleicoes": "Processo Eleitoral",
}

# Categorias canônicas conhecidas (destinos do mapa + outras já corretas)
CANONICAL_CATEGORIES: frozenset[str] = frozenset(CATEGORY_MAP.values())


# ─── Resultado ────────────────────────────────────────────────────────────────


@dataclass
class NormalizationReport:
    """Resumo das operações executadas pela normalização."""

    empty_pages_found: int = 0
    empty_pages_removed: int = 0
    empty_pages_inconsistencies: int = 0

    categories_normalized: int = 0          # contagem de páginas atualizadas
    category_mappings_applied: int = 0       # quantos pares (old → new) foram aplicados
    unmapped_categories_found: int = 0
    unmapped_categories_inconsistencies: int = 0

    dry_run: bool = False
    unmapped_categories: list[str] = field(default_factory=list)

    def summary(self) -> str:
        prefix = "[DRY-RUN] " if self.dry_run else ""
        lines = [
            f"{prefix}=== Relatório de Normalização ===",
            f"  Páginas vazias encontradas : {self.empty_pages_found}",
            f"  Páginas removidas          : {self.empty_pages_removed}",
            f"  Inconsistências (vazio)    : {self.empty_pages_inconsistencies}",
            "",
            f"  Pares de categoria mapeados: {self.category_mappings_applied}",
            f"  Páginas com categoria atualizada: {self.categories_normalized}",
            f"  Categorias não mapeadas    : {self.unmapped_categories_found}",
            f"  Inconsistências (categ.)   : {self.unmapped_categories_inconsistencies}",
        ]
        if self.unmapped_categories:
            lines.append(f"  Lista não mapeadas: {self.unmapped_categories}")
        return "\n".join(lines)


# ─── Normalizer ───────────────────────────────────────────────────────────────


class DataNormalizer:
    """Normaliza dados de páginas no PostgreSQL.

    Pode ser usado de forma programática ou via CLI (``main()``).
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # ── Auxiliares de inconsistência ──────────────────────────────────────────

    def _make_empty_content_inconsistency_values(
        self, url: str, title: str | None
    ) -> tuple:
        """Retorna a tupla de valores para INSERT na tabela data_inconsistencies."""
        now = datetime.now(timezone.utc)
        return (
            "page",                 # resource_type
            "warning",              # severity
            "empty_content",        # inconsistency_type
            url,                    # resource_url
            title,                  # resource_title
            None,                   # parent_page_url
            "Página sem categoria e sem conteúdo — removida na normalização",
            None,                   # http_status
            None,                   # error_message
            now,                    # detected_at
            "crawler",              # detected_by
            "open",                 # status
            0,                      # retry_count
            now,                    # last_checked_at
        )

    def _make_category_mismatch_values(
        self, category: str, page_count: int
    ) -> tuple:
        """Retorna a tupla de valores para INSERT na tabela data_inconsistencies."""
        now = datetime.now(timezone.utc)
        # Usa a categoria como resource_url (não há URL individual — agrupa por categoria)
        pseudo_url = f"category://{category}"
        return (
            "page",                 # resource_type
            "warning",              # severity
            "category_mismatch",    # inconsistency_type
            pseudo_url,             # resource_url
            category,               # resource_title (reutilizamos o campo)
            None,                   # parent_page_url
            f"Categoria '{category}' não mapeada — {page_count} página(s) afetada(s)",
            None,                   # http_status
            None,                   # error_message
            now,                    # detected_at
            "crawler",              # detected_by
            "open",                 # status
            0,                      # retry_count
            now,                    # last_checked_at
        )

    # ── Operação 1: remover páginas vazias ───────────────────────────────────

    async def remove_empty_pages(self, dry_run: bool = False) -> NormalizationReport:
        """Remove páginas sem categoria e sem conteúdo, registrando inconsistências.

        Uma página é considerada "vazia" se:
        - main_content IS NULL ou ''  (sem conteúdo extraído)
        - category      IS NULL ou ''  (não categorizada)
        """
        report = NormalizationReport(dry_run=dry_run)

        async with self._pool.acquire() as conn:
            # Busca páginas vazias
            rows = await conn.fetch(
                """
                SELECT id, url, title
                FROM pages
                WHERE (main_content IS NULL OR main_content = '')
                  AND (category     IS NULL OR category     = '')
                ORDER BY id
                """
            )
            report.empty_pages_found = len(rows)

            if not rows:
                logger.info("Nenhuma página vazia encontrada.")
                return report

            logger.info("%d página(s) vazia(s) encontrada(s).", len(rows))

            if dry_run:
                logger.info("[DRY-RUN] Nenhuma alteração gravada.")
                return report

            async with conn.transaction():
                # Registra inconsistências ANTES de remover
                for row in rows:
                    values = self._make_empty_content_inconsistency_values(
                        row["url"], row["title"]
                    )
                    await conn.execute(
                        """
                        INSERT INTO data_inconsistencies (
                            resource_type, severity, inconsistency_type,
                            resource_url, resource_title, parent_page_url,
                            detail, http_status, error_message,
                            detected_at, detected_by,
                            status, retry_count, last_checked_at
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6,
                            $7, $8, $9,
                            $10, $11,
                            $12, $13, $14
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        *values,
                    )
                    report.empty_pages_inconsistencies += 1

                # Remove as páginas (CASCADE cuida das FKs)
                ids = [row["id"] for row in rows]
                result = await conn.execute(
                    "DELETE FROM pages WHERE id = ANY($1::int[])", ids
                )
                # asyncpg retorna "DELETE N"
                try:
                    report.empty_pages_removed = int(result.split()[-1])
                except (ValueError, IndexError):
                    report.empty_pages_removed = len(ids)

        logger.info(
            "Removidas %d página(s) vazia(s); %d inconsistência(s) registrada(s).",
            report.empty_pages_removed,
            report.empty_pages_inconsistencies,
        )
        return report

    # ── Operação 2: normalizar categorias ────────────────────────────────────

    async def normalize_categories(self, dry_run: bool = False) -> NormalizationReport:
        """Normaliza categorias conforme CATEGORY_MAP e registra as não mapeadas.

        - Categorias em CATEGORY_MAP.keys() → substituídas pelo valor canônico.
        - Categorias em CANONICAL_CATEGORIES (destinos) → mantidas sem alteração.
        - Demais categorias → registradas como inconsistência 'category_mismatch'.
        """
        report = NormalizationReport(dry_run=dry_run)

        async with self._pool.acquire() as conn:
            # Lista todas as categorias distintas presentes (excl. NULL/'')
            cat_rows = await conn.fetch(
                """
                SELECT category, COUNT(*) AS cnt
                FROM pages
                WHERE category IS NOT NULL AND category != ''
                GROUP BY category
                ORDER BY category
                """
            )

            all_categories: dict[str, int] = {
                r["category"]: r["cnt"] for r in cat_rows
            }

            # Separa em: a normalizar, já canônicas, não mapeadas
            to_normalize = {
                cat: (CATEGORY_MAP[cat], cnt)
                for cat, cnt in all_categories.items()
                if cat in CATEGORY_MAP
            }
            unmapped = {
                cat: cnt
                for cat, cnt in all_categories.items()
                if cat not in CATEGORY_MAP and cat not in CANONICAL_CATEGORIES
            }

            logger.info(
                "Categorias: %d total | %d a normalizar | %d já canônicas | %d não mapeadas",
                len(all_categories),
                len(to_normalize),
                len(all_categories) - len(to_normalize) - len(unmapped),
                len(unmapped),
            )

            report.unmapped_categories_found = len(unmapped)
            report.unmapped_categories = sorted(unmapped.keys())
            report.category_mappings_applied = len(to_normalize)

            if dry_run:
                logger.info("[DRY-RUN] Nenhuma alteração gravada.")
                return report

            async with conn.transaction():
                # Aplica normalizações
                for old_cat, (new_cat, _cnt) in to_normalize.items():
                    result = await conn.execute(
                        "UPDATE pages SET category = $1 WHERE category = $2",
                        new_cat,
                        old_cat,
                    )
                    try:
                        n = int(result.split()[-1])
                    except (ValueError, IndexError):
                        n = 0
                    report.categories_normalized += n
                    logger.debug("  '%s' → '%s' (%d páginas)", old_cat, new_cat, n)

                # Registra categorias não mapeadas como inconsistências
                for cat, cnt in unmapped.items():
                    values = self._make_category_mismatch_values(cat, cnt)
                    await conn.execute(
                        """
                        INSERT INTO data_inconsistencies (
                            resource_type, severity, inconsistency_type,
                            resource_url, resource_title, parent_page_url,
                            detail, http_status, error_message,
                            detected_at, detected_by,
                            status, retry_count, last_checked_at
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6,
                            $7, $8, $9,
                            $10, $11,
                            $12, $13, $14
                        )
                        ON CONFLICT DO NOTHING
                        """,
                        *values,
                    )
                    report.unmapped_categories_inconsistencies += 1
                    logger.warning("  Categoria não mapeada: '%s' (%d páginas)", cat, cnt)

        logger.info(
            "Normalizadas %d página(s); %d inconsistência(s) de categoria registrada(s).",
            report.categories_normalized,
            report.unmapped_categories_inconsistencies,
        )
        return report

    # ── Execução completa ─────────────────────────────────────────────────────

    async def run(self, dry_run: bool = False) -> NormalizationReport:
        """Executa todas as operações de normalização em sequência."""
        report_empty = await self.remove_empty_pages(dry_run=dry_run)
        report_cats = await self.normalize_categories(dry_run=dry_run)

        # Combina relatórios
        combined = NormalizationReport(
            empty_pages_found=report_empty.empty_pages_found,
            empty_pages_removed=report_empty.empty_pages_removed,
            empty_pages_inconsistencies=report_empty.empty_pages_inconsistencies,
            categories_normalized=report_cats.categories_normalized,
            category_mappings_applied=report_cats.category_mappings_applied,
            unmapped_categories_found=report_cats.unmapped_categories_found,
            unmapped_categories_inconsistencies=report_cats.unmapped_categories_inconsistencies,
            dry_run=dry_run,
            unmapped_categories=report_cats.unmapped_categories,
        )
        return combined


# ─── CLI ──────────────────────────────────────────────────────────────────────


def _build_settings(database_url: str | None) -> Settings:
    """Constrói Settings, opcionalmente sobrescrevendo a database_url."""
    kwargs: dict = {}
    if database_url:
        kwargs["database_url"] = database_url
    return Settings(**kwargs)  # type: ignore[call-arg]


async def _async_main(args: argparse.Namespace) -> int:
    settings = _build_settings(args.database_url)
    pool = await asyncpg.create_pool(
        dsn=settings.database_url.replace(
            "postgresql+asyncpg://", "postgresql://"
        ),
        min_size=1,
        max_size=3,
    )
    try:
        normalizer = DataNormalizer(pool)
        report = await normalizer.run(dry_run=args.dry_run)
        print(report.summary())
        return 0
    finally:
        await pool.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Normaliza dados de páginas no banco Cristal (Etapa 8 Pipeline V2)."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Simula as operações sem gravar nada no banco.",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        metavar="URL",
        help="Database URL (sobrescreve CRISTAL_DATABASE_URL / .env).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(_async_main(args)))


if __name__ == "__main__":
    main()
