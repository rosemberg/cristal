"""Testes de integração — Migration 003: FTS com unaccent (Fase 1 RAG V2).

Critérios de aceite:
- Extension unaccent instalada após migration 003
- Configuração de texto cristal_pt existe no pg_catalog
- cristal_pt usa unaccent no mapeamento de palavras
- plainto_tsquery('cristal_pt', 'diárias') produz o mesmo lexema que 'DIARIAS'
- Busca em pages: texto 'DIARIAS' é encontrado por query 'diárias' (e vice-versa)
- Busca em document_chunks: mesmo comportamento
- Downgrade para 002 remove cristal_pt e unaccent, restaura triggers com 'portuguese'
"""

from __future__ import annotations

import asyncio

import asyncpg
import pytest
from alembic import command

from tests.integration.conftest import docker_required
from tests.integration.test_migrations import (
    PROJECT_ROOT,
    _asyncpg_dsn,
    _extension_exists,
    make_alembic_config,
)

# Revision IDs definidos na migration
REV_003 = "c3d4e5f6a1b2"
REV_002 = "b2c3d4e5f6a1"


# ─── Helpers ──────────────────────────────────────────────────────────────────


async def _ts_config_exists(dsn: str, config_name: str) -> bool:
    """Verifica se uma text search configuration existe no pg_catalog."""
    conn = await asyncpg.connect(dsn)
    try:
        result = await conn.fetchval(
            "SELECT COUNT(*) FROM pg_ts_config WHERE cfgname = $1",
            config_name,
        )
        return bool(result)
    finally:
        await conn.close()


async def _ts_config_uses_unaccent(dsn: str, config_name: str) -> bool:
    """Verifica se a config faz mapeamento com 'unaccent' para tokens de palavra."""
    conn = await asyncpg.connect(dsn)
    try:
        # pg_ts_config_map lista os dicionários por token type
        result = await conn.fetchval(
            """
            SELECT COUNT(*)
            FROM pg_ts_config_map m
            JOIN pg_ts_config c ON c.oid = m.mapcfg
            JOIN pg_ts_dict d   ON d.oid = ANY(m.mapdict)
            WHERE c.cfgname = $1
              AND d.dictname = 'unaccent'
            """,
            config_name,
        )
        return bool(result)
    finally:
        await conn.close()


async def _same_lexeme(dsn: str, term_a: str, term_b: str) -> bool:
    """Verifica se dois termos produzem o mesmo tsvector com cristal_pt."""
    conn = await asyncpg.connect(dsn)
    try:
        vec_a = await conn.fetchval(
            "SELECT plainto_tsquery('cristal_pt', $1)::text", term_a
        )
        vec_b = await conn.fetchval(
            "SELECT plainto_tsquery('cristal_pt', $1)::text", term_b
        )
        return vec_a == vec_b
    finally:
        await conn.close()


async def _insert_page_and_chunk(
    dsn: str,
    page_url: str,
    page_title: str,
    chunk_text: str,
) -> None:
    """Insere página e chunk para os testes de busca FTS."""
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO pages (url, title) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            page_url, page_title,
        )
        await conn.execute(
            "INSERT INTO document_contents (document_url, document_title, document_type)"
            " VALUES ($1, $2, 'csv') ON CONFLICT DO NOTHING",
            f"{page_url}/doc.csv", "Doc FTS Test",
        )
        await conn.execute(
            """
            INSERT INTO document_chunks (document_url, chunk_index, chunk_text)
            VALUES ($1, 0, $2)
            ON CONFLICT DO NOTHING
            """,
            f"{page_url}/doc.csv", chunk_text,
        )
    finally:
        await conn.close()


async def _cleanup_page(dsn: str, page_url: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "DELETE FROM document_contents WHERE document_url = $1",
            f"{page_url}/doc.csv",
        )
        await conn.execute("DELETE FROM pages WHERE url = $1", page_url)
    finally:
        await conn.close()


async def _fts_pages_finds(dsn: str, query: str, expected_url: str) -> bool:
    """Retorna True se a busca FTS em pages encontra a URL esperada."""
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT url FROM pages
            WHERE search_vector @@ plainto_tsquery('cristal_pt', $1)
            """,
            query,
        )
        return any(r["url"] == expected_url for r in rows)
    finally:
        await conn.close()


async def _fts_chunks_finds(dsn: str, query: str, expected_doc_url: str) -> bool:
    """Retorna True se a busca FTS em document_chunks encontra o documento esperado."""
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT document_url FROM document_chunks
            WHERE search_vector @@ plainto_tsquery('cristal_pt', $1)
            """,
            query,
        )
        return any(r["document_url"] == expected_doc_url for r in rows)
    finally:
        await conn.close()


async def _trigger_uses_config(dsn: str, function_name: str, config_name: str) -> bool:
    """Verifica se o corpo da função trigger contém a configuração FTS esperada."""
    conn = await asyncpg.connect(dsn)
    try:
        body = await conn.fetchval(
            "SELECT prosrc FROM pg_proc WHERE proname = $1",
            function_name,
        )
        return body is not None and config_name in body
    finally:
        await conn.close()


# ─── Fixture de ciclo de vida ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def migrated_003(pg_settings):  # type: ignore[no-untyped-def]
    """Aplica upgrade head (001 + 002 + 003) e faz downgrade no teardown."""
    dsn_url = pg_settings.database_url
    cfg = make_alembic_config(dsn_url)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    yield cfg, dsn_url

    command.downgrade(cfg, "base")


# ─── Testes: extensão unaccent ────────────────────────────────────────────────


@docker_required
def test_extensao_unaccent_instalada(migrated_003):  # type: ignore[no-untyped-def]
    """A extensão unaccent deve estar instalada após migration 003."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    exists = asyncio.run(_extension_exists(dsn, "unaccent"))
    assert exists, "Extensão unaccent não encontrada após migration 003"


# ─── Testes: configuração cristal_pt ──────────────────────────────────────────


@docker_required
def test_config_cristal_pt_existe(migrated_003):  # type: ignore[no-untyped-def]
    """A configuração FTS cristal_pt deve existir no pg_catalog após migration 003."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    exists = asyncio.run(_ts_config_exists(dsn, "cristal_pt"))
    assert exists, "Text search config 'cristal_pt' não encontrada no pg_catalog"


@docker_required
def test_cristal_pt_usa_unaccent_no_mapeamento(migrated_003):  # type: ignore[no-untyped-def]
    """cristal_pt deve mapear tokens de palavra através do dicionário unaccent."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    uses_unaccent = asyncio.run(_ts_config_uses_unaccent(dsn, "cristal_pt"))
    assert uses_unaccent, "cristal_pt não usa 'unaccent' no mapeamento de palavras"


# ─── Testes: normalização de acentos (cenário central da Fase 1) ──────────────


@docker_required
def test_diarias_com_acento_e_sem_acento_produzem_mesmo_lexema(migrated_003):  # type: ignore[no-untyped-def]
    """plainto_tsquery('cristal_pt', 'diárias') deve produzir o mesmo lexema que 'DIARIAS'.

    Este é o cenário central da Fase 1: CSVs armazenam 'DIARIAS' (sem acento,
    caixa alta) enquanto o usuário pesquisa 'diárias' (com acento). Sem unaccent,
    o stemmer português gera lexemas diferentes ('diár' vs 'diari').
    """
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    same = asyncio.run(_same_lexeme(dsn, "diárias", "DIARIAS"))
    assert same, (
        "plainto_tsquery('cristal_pt', 'diárias') e 'DIARIAS' produziram lexemas diferentes. "
        "O unaccent não está sendo aplicado antes do stemmer."
    )


@docker_required
def test_licoes_e_licoes_sem_acento_mesmo_lexema(migrated_003):  # type: ignore[no-untyped-def]
    """Normalização deve funcionar para outros pares comuns em dados de transparência."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    same = asyncio.run(_same_lexeme(dsn, "licitações", "LICITACOES"))
    assert same, "licitações/LICITACOES não produziram o mesmo lexema com cristal_pt"


# ─── Testes: trigger pages_search_trigger ─────────────────────────────────────


@docker_required
def test_trigger_pages_usa_cristal_pt(migrated_003):  # type: ignore[no-untyped-def]
    """O trigger pages_search_trigger deve usar 'cristal_pt', não 'portuguese'."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    uses = asyncio.run(_trigger_uses_config(dsn, "pages_search_trigger", "cristal_pt"))
    assert uses, "pages_search_trigger ainda usa 'portuguese' em vez de 'cristal_pt'"


@docker_required
def test_trigger_chunks_usa_cristal_pt(migrated_003):  # type: ignore[no-untyped-def]
    """O trigger chunks_search_trigger deve usar 'cristal_pt', não 'portuguese'."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    uses = asyncio.run(_trigger_uses_config(dsn, "chunks_search_trigger", "cristal_pt"))
    assert uses, "chunks_search_trigger ainda usa 'portuguese' em vez de 'cristal_pt'"


# ─── Testes: busca FTS em pages ────────────────────────────────────────────────


@docker_required
def test_fts_pages_acentuado_encontra_texto_sem_acento(migrated_003):  # type: ignore[no-untyped-def]
    """Query 'diárias' deve encontrar página cujo título contém 'DIARIAS' (sem acento)."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    page_url = "https://www.tre-pi.jus.br/test-fts-pages-acentuado"

    asyncio.run(_insert_page_and_chunk(dsn, page_url, "DIARIAS PAGAS 2024", "conteudo auxiliar"))
    try:
        found = asyncio.run(_fts_pages_finds(dsn, "diárias", page_url))
        assert found, (
            "Query 'diárias' não encontrou página com título 'DIARIAS PAGAS 2024'. "
            "O trigger pages_search_trigger não aplica cristal_pt corretamente."
        )
    finally:
        asyncio.run(_cleanup_page(dsn, page_url))


@docker_required
def test_fts_pages_sem_acento_encontra_texto_acentuado(migrated_003):  # type: ignore[no-untyped-def]
    """Query 'DIARIAS' deve encontrar página cujo título contém 'diárias' (com acento)."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    page_url = "https://www.tre-pi.jus.br/test-fts-pages-sem-acento"

    asyncio.run(_insert_page_and_chunk(dsn, page_url, "diárias pagas 2024", "conteudo auxiliar"))
    try:
        found = asyncio.run(_fts_pages_finds(dsn, "DIARIAS", page_url))
        assert found, (
            "Query 'DIARIAS' não encontrou página com título 'diárias pagas 2024'."
        )
    finally:
        asyncio.run(_cleanup_page(dsn, page_url))


# ─── Testes: busca FTS em document_chunks ─────────────────────────────────────


@docker_required
def test_fts_chunks_acentuado_encontra_texto_sem_acento(migrated_003):  # type: ignore[no-untyped-def]
    """Query 'diárias' deve encontrar chunk cujo texto contém 'DIARIAS' (como nos CSVs)."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    page_url = "https://www.tre-pi.jus.br/test-fts-chunks-acentuado"
    doc_url = f"{page_url}/doc.csv"

    asyncio.run(_insert_page_and_chunk(
        dsn, page_url, "Transparência",
        "DIARIAS PAGAS AO SERVIDOR CONFORME PORTARIA",
    ))
    try:
        found = asyncio.run(_fts_chunks_finds(dsn, "diárias", doc_url))
        assert found, (
            "Query 'diárias' não encontrou chunk com texto 'DIARIAS PAGAS AO SERVIDOR'. "
            "Este é o cenário real dos CSVs do TRE-PI."
        )
    finally:
        asyncio.run(_cleanup_page(dsn, page_url))


@docker_required
def test_fts_chunks_sem_acento_encontra_texto_acentuado(migrated_003):  # type: ignore[no-untyped-def]
    """Query 'DIARIAS' deve encontrar chunk cujo texto contém 'diárias' (com acento)."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    page_url = "https://www.tre-pi.jus.br/test-fts-chunks-sem-acento"
    doc_url = f"{page_url}/doc.csv"

    asyncio.run(_insert_page_and_chunk(
        dsn, page_url, "Transparência",
        "Pagamento de diárias ao servidor conforme portaria",
    ))
    try:
        found = asyncio.run(_fts_chunks_finds(dsn, "DIARIAS", doc_url))
        assert found, (
            "Query 'DIARIAS' não encontrou chunk com texto 'diárias' (com acento)."
        )
    finally:
        asyncio.run(_cleanup_page(dsn, page_url))


@docker_required
def test_fts_chunks_licitacoes_normalizado(migrated_003):  # type: ignore[no-untyped-def]
    """Query 'licitações' deve encontrar chunk com 'LICITACOES' e vice-versa."""
    _cfg, dsn_url = migrated_003
    dsn = _asyncpg_dsn(dsn_url)
    page_url = "https://www.tre-pi.jus.br/test-fts-chunks-licitacoes"
    doc_url = f"{page_url}/doc.csv"

    asyncio.run(_insert_page_and_chunk(
        dsn, page_url, "Transparência",
        "LICITACOES REALIZADAS PELO TRE-PI NO EXERCICIO 2024",
    ))
    try:
        found = asyncio.run(_fts_chunks_finds(dsn, "licitações", doc_url))
        assert found, "Query 'licitações' não encontrou chunk com 'LICITACOES'."
    finally:
        asyncio.run(_cleanup_page(dsn, page_url))


# ─── Teste de downgrade ───────────────────────────────────────────────────────


@docker_required
def test_downgrade_003_remove_cristal_pt_e_unaccent(pg_settings):  # type: ignore[no-untyped-def]
    """Após downgrade para 002, cristal_pt e unaccent devem ser removidos."""
    dsn_url = pg_settings.database_url
    cfg = make_alembic_config(dsn_url)

    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    # Downgrade de volta para revision 002
    command.downgrade(cfg, REV_002)

    dsn = _asyncpg_dsn(dsn_url)

    config_exists = asyncio.run(_ts_config_exists(dsn, "cristal_pt"))
    assert not config_exists, "cristal_pt ainda existe após downgrade para 002"

    unaccent_exists = asyncio.run(_extension_exists(dsn, "unaccent"))
    assert not unaccent_exists, "Extensão unaccent ainda existe após downgrade para 002"

    # Triggers devem ter voltado para 'portuguese'
    pages_uses_portuguese = asyncio.run(
        _trigger_uses_config(dsn, "pages_search_trigger", "portuguese")
    )
    assert pages_uses_portuguese, "pages_search_trigger não voltou para 'portuguese' após downgrade"

    chunks_uses_portuguese = asyncio.run(
        _trigger_uses_config(dsn, "chunks_search_trigger", "portuguese")
    )
    assert chunks_uses_portuguese, "chunks_search_trigger não voltou para 'portuguese' após downgrade"

    # Cleanup
    command.downgrade(cfg, "base")
