"""Testes de integração — Etapa 8: normalização de dados (normalize_data.py).

TDD: testes escritos antes da implementação final.

Critérios de aceite:
- Páginas sem categoria E sem conteúdo são identificadas corretamente
- Essas páginas são removidas do banco
- Inconsistência 'empty_content' é registrada para cada página removida
- Categorias mapeadas em CATEGORY_MAP são renomeadas para o nome canônico
- O search_vector é atualizado automaticamente pelo trigger do banco
- Categorias não mapeadas (nem em CATEGORY_MAP, nem canônicas) geram 'category_mismatch'
- Páginas com categoria canônica já correta não são alteradas
- Dry-run não grava nada no banco
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from scripts.normalize_data import (
    CANONICAL_CATEGORIES,
    CATEGORY_MAP,
    DataNormalizer,
)
from tests.integration.conftest import docker_required

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ─── Helpers de Alembic ───────────────────────────────────────────────────────


def _make_alembic_config(database_url: str) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def _asyncpg_dsn(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://")


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def run_migrations(pg_settings):  # type: ignore[misc]
    cfg = _make_alembic_config(pg_settings.database_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield
    command.downgrade(cfg, "base")


@pytest.fixture
async def pool(pg_settings, run_migrations):  # type: ignore[misc]
    """Pool limpo por teste — trunca todas as tabelas."""
    p = await asyncpg.create_pool(
        dsn=_asyncpg_dsn(pg_settings.database_url),
        min_size=1,
        max_size=3,
    )
    async with p.acquire() as conn:
        await conn.execute(
            """
            TRUNCATE TABLE data_inconsistencies, query_logs, chat_messages, chat_sessions,
                document_tables, document_chunks, document_contents,
                page_links, navigation_tree, documents, pages
            RESTART IDENTITY CASCADE
            """
        )
    yield p
    await p.close()


@pytest.fixture
def normalizer(pool):  # type: ignore[misc]
    return DataNormalizer(pool)


# ─── Helpers de inserção ──────────────────────────────────────────────────────


async def _insert_page(
    conn: asyncpg.Connection,
    url: str,
    title: str = "Título",
    category: str | None = None,
    main_content: str | None = None,
) -> int:
    row_id: int = await conn.fetchval(
        """
        INSERT INTO pages (url, title, category, main_content)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        url, title, category, main_content,
    )
    return row_id


async def _count_pages(conn: asyncpg.Connection) -> int:
    return await conn.fetchval("SELECT COUNT(*) FROM pages")


async def _count_inconsistencies(
    conn: asyncpg.Connection,
    inconsistency_type: str | None = None,
) -> int:
    if inconsistency_type:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM data_inconsistencies WHERE inconsistency_type = $1",
            inconsistency_type,
        )
    return await conn.fetchval("SELECT COUNT(*) FROM data_inconsistencies")


async def _get_category(conn: asyncpg.Connection, url: str) -> str | None:
    return await conn.fetchval("SELECT category FROM pages WHERE url = $1", url)


# ─── Testes: remoção de páginas vazias ───────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_remove_empty_pages_identifica_paginas_sem_conteudo_e_categoria(
    pool, normalizer: DataNormalizer
) -> None:
    """Páginas com content=NULL e category=NULL devem ser encontradas."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/vazia", "Vazia")  # sem cat, sem content
        await _insert_page(conn, "https://tre-pi.jus.br/com-cat", "Com Cat",
                           category="Gestão de Pessoas")  # tem categoria

    report = await normalizer.remove_empty_pages()
    assert report.empty_pages_found == 1


@docker_required
@pytest.mark.integration
async def test_remove_empty_pages_nao_conta_paginas_com_conteudo(
    pool, normalizer: DataNormalizer
) -> None:
    """Página sem categoria mas COM conteúdo não deve ser removida."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/so-conteudo",
                           main_content="Algum conteúdo aqui")

    report = await normalizer.remove_empty_pages()
    assert report.empty_pages_found == 0


@docker_required
@pytest.mark.integration
async def test_remove_empty_pages_apaga_paginas_do_banco(
    pool, normalizer: DataNormalizer
) -> None:
    """Páginas vazias devem ser deletadas da tabela pages."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/lixo1")
        await _insert_page(conn, "https://tre-pi.jus.br/lixo2")
        await _insert_page(conn, "https://tre-pi.jus.br/boa", category="Processo Eleitoral",
                           main_content="Conteúdo real")
        total_antes = await _count_pages(conn)

    assert total_antes == 3

    await normalizer.remove_empty_pages()

    async with pool.acquire() as conn:
        total_depois = await _count_pages(conn)

    assert total_depois == 1


@docker_required
@pytest.mark.integration
async def test_remove_empty_pages_registra_inconsistencia_empty_content(
    pool, normalizer: DataNormalizer
) -> None:
    """Cada página vazia deve gerar uma inconsistência 'empty_content'."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/lixo-a")
        await _insert_page(conn, "https://tre-pi.jus.br/lixo-b")

    await normalizer.remove_empty_pages()

    async with pool.acquire() as conn:
        cnt = await _count_inconsistencies(conn, "empty_content")

    assert cnt == 2


@docker_required
@pytest.mark.integration
async def test_remove_empty_pages_inconsistencia_tem_detected_by_crawler(
    pool, normalizer: DataNormalizer
) -> None:
    """Inconsistências de páginas vazias devem ter detected_by='crawler'."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/lixo-crawler")

    await normalizer.remove_empty_pages()

    async with pool.acquire() as conn:
        detected_by = await conn.fetchval(
            "SELECT detected_by FROM data_inconsistencies WHERE inconsistency_type = 'empty_content' LIMIT 1"
        )

    assert detected_by == "crawler"


@docker_required
@pytest.mark.integration
async def test_remove_empty_pages_dry_run_nao_remove_nem_registra(
    pool, normalizer: DataNormalizer
) -> None:
    """Dry-run não deve remover páginas nem inserir inconsistências."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/dry-lixo")

    report = await normalizer.remove_empty_pages(dry_run=True)

    async with pool.acquire() as conn:
        paginas = await _count_pages(conn)
        inconsistencias = await _count_inconsistencies(conn)

    assert report.dry_run is True
    assert report.empty_pages_found == 1
    assert report.empty_pages_removed == 0
    assert paginas == 1
    assert inconsistencias == 0


@docker_required
@pytest.mark.integration
async def test_remove_empty_pages_retorna_contagem_correta(
    pool, normalizer: DataNormalizer
) -> None:
    """O relatório deve refletir exatamente quantas páginas foram removidas."""
    async with pool.acquire() as conn:
        for i in range(5):
            await _insert_page(conn, f"https://tre-pi.jus.br/lixo-{i}")

    report = await normalizer.remove_empty_pages()

    assert report.empty_pages_found == 5
    assert report.empty_pages_removed == 5
    assert report.empty_pages_inconsistencies == 5


# ─── Testes: normalização de categorias ──────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_normalize_categories_renomeia_categoria_mapeada(
    pool, normalizer: DataNormalizer
) -> None:
    """'Despesas' deve ser renomeada para 'Gestão Orçamentária e Financeira'."""
    async with pool.acquire() as conn:
        await _insert_page(
            conn, "https://tre-pi.jus.br/desp",
            category="Despesas", main_content="Conteúdo"
        )

    await normalizer.normalize_categories()

    async with pool.acquire() as conn:
        cat = await _get_category(conn, "https://tre-pi.jus.br/desp")

    assert cat == "Gestão Orçamentária e Financeira"


@docker_required
@pytest.mark.integration
async def test_normalize_categories_agrupa_multiplas_categorias_no_mesmo_canonico(
    pool, normalizer: DataNormalizer
) -> None:
    """'Contratos', 'Convenios' e 'Instrumentos De Cooperacao' → mesmo canônico."""
    canonical = "Licitações, Contratos e Instrumentos de Cooperação"
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/contratos",
                           category="Contratos", main_content="x")
        await _insert_page(conn, "https://tre-pi.jus.br/convenios",
                           category="Convenios", main_content="x")
        await _insert_page(conn, "https://tre-pi.jus.br/instrumentos",
                           category="Instrumentos De Cooperacao", main_content="x")

    await normalizer.normalize_categories()

    async with pool.acquire() as conn:
        cats = await conn.fetch(
            "SELECT DISTINCT category FROM pages ORDER BY category"
        )

    assert [r["category"] for r in cats] == [canonical]


@docker_required
@pytest.mark.integration
async def test_normalize_categories_mantem_categoria_canonica_intocada(
    pool, normalizer: DataNormalizer
) -> None:
    """Página com categoria já canônica não deve ser alterada."""
    async with pool.acquire() as conn:
        await _insert_page(
            conn, "https://tre-pi.jus.br/ja-ok",
            category="Processo Eleitoral", main_content="Conteúdo"
        )

    report = await normalizer.normalize_categories()
    # 'Processo Eleitoral' é destino do mapa → não conta como "a normalizar"

    async with pool.acquire() as conn:
        cat = await _get_category(conn, "https://tre-pi.jus.br/ja-ok")

    assert cat == "Processo Eleitoral"
    # A página não foi contabilizada em categories_normalized (não estava mapeada)
    assert report.categories_normalized == 0


@docker_required
@pytest.mark.integration
async def test_normalize_categories_registra_categoria_nao_mapeada(
    pool, normalizer: DataNormalizer
) -> None:
    """Categoria desconhecida deve gerar inconsistência 'category_mismatch'."""
    async with pool.acquire() as conn:
        await _insert_page(
            conn, "https://tre-pi.jus.br/desconhecida",
            category="Categoria Estranha", main_content="x"
        )

    await normalizer.normalize_categories()

    async with pool.acquire() as conn:
        cnt = await _count_inconsistencies(conn, "category_mismatch")

    assert cnt == 1


@docker_required
@pytest.mark.integration
async def test_normalize_categories_inconsistencia_contem_nome_da_categoria(
    pool, normalizer: DataNormalizer
) -> None:
    """A inconsistência deve mencionar o nome da categoria desconhecida."""
    async with pool.acquire() as conn:
        await _insert_page(
            conn, "https://tre-pi.jus.br/nova-cat",
            category="Nova Categoria Desconhecida", main_content="x"
        )

    await normalizer.normalize_categories()

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT resource_title, detail FROM data_inconsistencies WHERE inconsistency_type = 'category_mismatch'"
        )

    assert row is not None
    assert "Nova Categoria Desconhecida" in row["resource_title"]
    assert "Nova Categoria Desconhecida" in row["detail"]


@docker_required
@pytest.mark.integration
async def test_normalize_categories_dry_run_nao_grava(
    pool, normalizer: DataNormalizer
) -> None:
    """Dry-run não deve alterar categorias nem registrar inconsistências."""
    async with pool.acquire() as conn:
        await _insert_page(
            conn, "https://tre-pi.jus.br/dry-cat",
            category="Despesas", main_content="x"
        )

    report = await normalizer.normalize_categories(dry_run=True)

    async with pool.acquire() as conn:
        cat = await _get_category(conn, "https://tre-pi.jus.br/dry-cat")
        inconsistencias = await _count_inconsistencies(conn)

    assert report.dry_run is True
    assert cat == "Despesas"   # não alterada
    assert inconsistencias == 0


@docker_required
@pytest.mark.integration
async def test_normalize_categories_retorna_lista_nao_mapeadas(
    pool, normalizer: DataNormalizer
) -> None:
    """O relatório deve listar as categorias desconhecidas encontradas."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/unk1",
                           category="Categoria X", main_content="x")
        await _insert_page(conn, "https://tre-pi.jus.br/unk2",
                           category="Categoria Y", main_content="x")

    report = await normalizer.normalize_categories()

    assert report.unmapped_categories_found == 2
    assert "Categoria X" in report.unmapped_categories
    assert "Categoria Y" in report.unmapped_categories


@docker_required
@pytest.mark.integration
async def test_normalize_categories_aplica_todos_os_mapeamentos(
    pool, normalizer: DataNormalizer
) -> None:
    """Todos os 19 pares em CATEGORY_MAP devem ser aplicados quando presentes."""
    async with pool.acquire() as conn:
        for i, old_cat in enumerate(CATEGORY_MAP.keys()):
            await _insert_page(
                conn,
                f"https://tre-pi.jus.br/norm-{i}",
                category=old_cat,
                main_content="Conteúdo de teste",
            )

    report = await normalizer.normalize_categories()

    assert report.category_mappings_applied == len(CATEGORY_MAP)

    # Verifica que nenhuma categoria antiga permanece
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT category FROM pages ORDER BY category"
        )
    remaining_cats = {r["category"] for r in rows}

    for old_cat in CATEGORY_MAP:
        assert old_cat not in remaining_cats, (
            f"Categoria antiga '{old_cat}' ainda presente após normalização"
        )

    # Todas as categorias restantes devem ser canônicas
    for cat in remaining_cats:
        assert cat in CANONICAL_CATEGORIES, (
            f"Categoria '{cat}' não é canônica após normalização"
        )


# ─── Testes: run() completo ───────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_run_combina_remocao_e_normalizacao(
    pool, normalizer: DataNormalizer
) -> None:
    """run() deve executar as duas etapas e retornar relatório combinado."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/run-lixo")   # vazia
        await _insert_page(conn, "https://tre-pi.jus.br/run-desp",
                           category="Despesas", main_content="x")   # a normalizar

    report = await normalizer.run()

    assert report.empty_pages_found == 1
    assert report.empty_pages_removed == 1
    assert report.categories_normalized == 1
    assert report.dry_run is False


@docker_required
@pytest.mark.integration
async def test_run_dry_run_nao_altera_banco(
    pool, normalizer: DataNormalizer
) -> None:
    """run(dry_run=True) não deve alterar nada no banco."""
    async with pool.acquire() as conn:
        await _insert_page(conn, "https://tre-pi.jus.br/run-dry-lixo")
        await _insert_page(conn, "https://tre-pi.jus.br/run-dry-desp",
                           category="Despesas", main_content="x")

    report = await normalizer.run(dry_run=True)

    async with pool.acquire() as conn:
        paginas = await _count_pages(conn)
        inconsistencias = await _count_inconsistencies(conn)
        cat = await _get_category(conn, "https://tre-pi.jus.br/run-dry-desp")

    assert report.dry_run is True
    assert paginas == 2          # nenhuma removida
    assert inconsistencias == 0  # nenhuma registrada
    assert cat == "Despesas"     # não normalizada


# ─── Testes: constantes do módulo ─────────────────────────────────────────────


def test_category_map_contem_19_entradas() -> None:
    """CATEGORY_MAP deve ter exatamente 19 mapeamentos conforme o plano."""
    assert len(CATEGORY_MAP) == 19


def test_canonical_categories_contem_destinos_do_mapa() -> None:
    """Todos os valores do CATEGORY_MAP devem estar em CANONICAL_CATEGORIES."""
    for dest in CATEGORY_MAP.values():
        assert dest in CANONICAL_CATEGORIES, (
            f"Destino '{dest}' não está em CANONICAL_CATEGORIES"
        )


def test_nenhuma_chave_do_mapa_esta_em_canonical() -> None:
    """Nenhum nome antigo (chave) deve coincidir com um nome canônico."""
    for old_cat in CATEGORY_MAP:
        assert old_cat not in CANONICAL_CATEGORIES, (
            f"Chave '{old_cat}' está erroneamente em CANONICAL_CATEGORIES"
        )
