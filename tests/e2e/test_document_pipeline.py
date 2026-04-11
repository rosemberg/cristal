"""Testes E2E — Pipeline de ingestão de documentos (Etapa 9 Pipeline V2).

Critérios de aceite:
- Dado um documento pendente → pipeline baixa, processa e persiste
- Após ingestão, document_chunks tem registros no banco
- Após ingestão, document_contents tem status='done'
- search_tables() retorna tabelas extraídas de documentos processados
- Documento com erro de download → inconsistência registrada em data_inconsistencies
- get_status() reflete corretamente os contadores do pipeline
"""

from __future__ import annotations

from pathlib import Path

import asyncpg
import pytest

from app.adapters.outbound.postgres.document_repo import PostgresDocumentRepository
from app.adapters.outbound.postgres.inconsistency_repo import PostgresInconsistencyRepository
from app.adapters.outbound.postgres.search_repo import PostgresSearchRepository
from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document_table import DocumentTable
from app.domain.ports.outbound.document_process_gateway import DocumentProcessGateway
from app.domain.ports.outbound.document_repository import ProcessedDocument
from app.domain.services.document_ingestion_service import DocumentIngestionService
from tests.conftest import FakeDocumentProcessGateway
from tests.e2e.conftest import FakeDownloadGateway, docker_required


# ─── Helpers de seed ──────────────────────────────────────────────────────────

PAGE_URL = "https://www.tre-pi.jus.br/transparencia"
PDF_URL = "https://www.tre-pi.jus.br/docs/relatorio-anual.pdf"
CSV_URL = "https://www.tre-pi.jus.br/docs/estagiarios.csv"


async def _insert_page(conn: asyncpg.Connection, url: str = PAGE_URL) -> None:
    await conn.execute(
        """
        INSERT INTO pages (url, title, category)
        VALUES ($1, $2, $3)
        ON CONFLICT (url) DO NOTHING
        """,
        url, "Transparência — TRE-PI", "Gestão de Pessoas",
    )


async def _insert_document(
    conn: asyncpg.Connection,
    doc_url: str,
    page_url: str = PAGE_URL,
    doc_type: str = "pdf",
    status: str = "pending",
) -> None:
    await conn.execute(
        """
        INSERT INTO documents (page_url, document_url, document_title, document_type, processing_status)
        VALUES ($1, $2, $3, $4, $5)
        ON CONFLICT (page_url, document_url) DO NOTHING
        """,
        page_url,
        doc_url,
        Path(doc_url).name,
        doc_type,
        status,
    )


def _make_service(
    pool: asyncpg.Pool,
    downloader: FakeDownloadGateway | None = None,
    processor: DocumentProcessGateway | None = None,
) -> DocumentIngestionService:
    doc_repo = PostgresDocumentRepository(pool)
    inconsistency_repo = PostgresInconsistencyRepository(pool)
    return DocumentIngestionService(
        doc_repo=doc_repo,
        downloader=downloader or FakeDownloadGateway(),
        processor=processor or FakeDocumentProcessGateway(),
        inconsistency_repo=inconsistency_repo,
    )


# ─── Testes ───────────────────────────────────────────────────────────────────


@docker_required
@pytest.mark.integration
async def test_ingest_pending_processa_documento_e_atualiza_status(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Documento pendente → ingest_pending() → status='done'."""
    async with pool_e2e.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, PDF_URL)

    svc = _make_service(pool_e2e)
    stats = await svc.ingest_pending()

    assert stats.total == 1
    assert stats.processed == 1
    assert stats.errors == 0

    async with pool_e2e.acquire() as conn:
        status = await conn.fetchval(
            "SELECT processing_status FROM documents WHERE document_url = $1", PDF_URL
        )
    assert status == "done"


@docker_required
@pytest.mark.integration
async def test_ingest_pending_persiste_chunks_no_banco(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Após ingestão, document_chunks deve conter registros."""
    chunk_text = "Conteúdo do relatório anual — Exercício 2025."
    processor = FakeDocumentProcessGateway(
        result=ProcessedDocument(
            document_url=PDF_URL,
            text=chunk_text,
            chunks=[
                DocumentChunk(
                    id=0, document_url=PDF_URL,
                    chunk_index=0, text=chunk_text,
                    token_count=10,
                )
            ],
            tables=[],
            num_pages=3,
            title="Relatório Anual 2025",
        )
    )

    async with pool_e2e.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, PDF_URL)

    svc = _make_service(pool_e2e, processor=processor)
    await svc.ingest_pending()

    doc_repo = PostgresDocumentRepository(pool_e2e)
    chunks = await doc_repo.get_chunks(PDF_URL)

    assert len(chunks) == 1
    assert chunks[0].text == chunk_text


@docker_required
@pytest.mark.integration
async def test_ingest_pending_persiste_tabelas_no_banco(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Tabelas extraídas do documento devem ser persistidas em document_tables."""
    processor = FakeDocumentProcessGateway(
        result=ProcessedDocument(
            document_url=CSV_URL,
            text="tabela estagiários",
            # O pipeline exige ao menos 1 chunk para não rejeitar o documento
            chunks=[
                DocumentChunk(
                    id=0, document_url=CSV_URL,
                    chunk_index=0, text="Tabela de estagiários do TRE-PI 2025.",
                    token_count=8,
                )
            ],
            tables=[
                DocumentTable(
                    id=0, document_url=CSV_URL,
                    table_index=0,
                    headers=["Nome", "Matrícula", "Setor"],
                    rows=[["Maria Silva", "001", "TI"], ["João Costa", "002", "RH"]],
                    caption="Lista de Estagiários TRE-PI — 2025",
                    num_rows=2, num_cols=3,
                )
            ],
            num_pages=1,
            title="Estagiários CSV",
        )
    )

    async with pool_e2e.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, CSV_URL, doc_type="csv")

    svc = _make_service(pool_e2e, processor=processor)
    await svc.ingest_pending()

    doc_repo = PostgresDocumentRepository(pool_e2e)
    tables = await doc_repo.get_tables(CSV_URL)

    assert len(tables) == 1
    assert tables[0].caption == "Lista de Estagiários TRE-PI — 2025"
    assert tables[0].headers == ["Nome", "Matrícula", "Setor"]
    assert len(tables[0].rows) == 2


@docker_required
@pytest.mark.integration
async def test_search_tables_retorna_tabela_apos_ingestao(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Após ingestion, search_tables('estagiários') deve retornar a tabela."""
    processor = FakeDocumentProcessGateway(
        result=ProcessedDocument(
            document_url=CSV_URL,
            text="lista estagiários",
            # pipeline requer ao menos 1 chunk
            chunks=[
                DocumentChunk(
                    id=0, document_url=CSV_URL,
                    chunk_index=0, text="estagiários",
                    token_count=1,
                )
            ],
            tables=[
                DocumentTable(
                    id=0, document_url=CSV_URL,
                    table_index=0,
                    headers=["Nome", "Cargo"],
                    rows=[["Ana Souza", "Estagiária"]],
                    caption="Estagiários TRE-PI",
                    num_rows=1, num_cols=2,
                )
            ],
            num_pages=1,
        )
    )

    async with pool_e2e.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, CSV_URL, doc_type="csv")

    svc = _make_service(pool_e2e, processor=processor)
    await svc.ingest_pending()

    search_repo = PostgresSearchRepository(pool_e2e)
    results = await search_repo.search_tables("estagiários")

    assert len(results) >= 1
    assert any("Estagiários" in (t.caption or "") for t in results)


@docker_required
@pytest.mark.integration
async def test_ingest_pending_erro_de_download_registra_inconsistencia(
    pool_e2e: asyncpg.Pool,
) -> None:
    """Documento inacessível (404) deve gerar inconsistência em data_inconsistencies."""
    downloader = FakeDownloadGateway(broken_urls={PDF_URL})

    async with pool_e2e.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, PDF_URL)

    svc = _make_service(pool_e2e, downloader=downloader)
    stats = await svc.ingest_pending()

    assert stats.errors == 1

    async with pool_e2e.acquire() as conn:
        inc_count = await conn.fetchval(
            "SELECT COUNT(*) FROM data_inconsistencies WHERE resource_url = $1",
            PDF_URL,
        )
        status = await conn.fetchval(
            "SELECT processing_status FROM documents WHERE document_url = $1",
            PDF_URL,
        )

    assert inc_count >= 1, "Inconsistência não foi registrada para documento com erro de download"
    assert status == "error"


@docker_required
@pytest.mark.integration
async def test_get_status_reflete_contadores_corretos(
    pool_e2e: asyncpg.Pool,
) -> None:
    """get_status() deve refletir contagens atualizadas após ingestão."""
    processor = FakeDocumentProcessGateway(
        result=ProcessedDocument(
            document_url=PDF_URL,
            text="conteúdo",
            chunks=[DocumentChunk(id=0, document_url=PDF_URL, chunk_index=0, text="trecho", token_count=5)],
            tables=[],
            num_pages=1,
        )
    )

    async with pool_e2e.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, PDF_URL)
        # Insere um segundo documento que ficará pendente
        await _insert_document(conn, CSV_URL, doc_type="csv")

    svc = _make_service(pool_e2e, processor=processor)

    # Status antes da ingestão
    before = await svc.get_status()
    assert before.pending == 2

    # Processa apenas o PDF (CSV tem downloader normal, mas processador retorna fixed result)
    await svc.ingest_pending()

    after = await svc.get_status()
    assert after.pending == 0
    assert after.done == 2
    assert after.total_chunks >= 1


@docker_required
@pytest.mark.integration
async def test_ingest_single_processa_documento_por_url(
    pool_e2e: asyncpg.Pool,
) -> None:
    """ingest_single(url) deve processar apenas o documento solicitado."""
    async with pool_e2e.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, PDF_URL)
        await _insert_document(conn, CSV_URL, doc_type="csv")

    svc = _make_service(pool_e2e)
    success = await svc.ingest_single(PDF_URL)

    assert success is True

    async with pool_e2e.acquire() as conn:
        pdf_status = await conn.fetchval(
            "SELECT processing_status FROM documents WHERE document_url = $1", PDF_URL
        )
        csv_status = await conn.fetchval(
            "SELECT processing_status FROM documents WHERE document_url = $1", CSV_URL
        )

    assert pdf_status == "done"
    assert csv_status == "pending"  # não foi processado


@docker_required
@pytest.mark.integration
async def test_reprocess_errors_retenta_documentos_com_falha(
    pool_e2e: asyncpg.Pool,
) -> None:
    """reprocess_errors() deve tentar novamente documentos com status='error'."""
    # 1ª rodada: downloader quebrado → error
    broken_downloader = FakeDownloadGateway(broken_urls={PDF_URL})

    async with pool_e2e.acquire() as conn:
        await _insert_page(conn)
        await _insert_document(conn, PDF_URL)

    svc_broken = _make_service(pool_e2e, downloader=broken_downloader)
    await svc_broken.ingest_pending()

    async with pool_e2e.acquire() as conn:
        assert await conn.fetchval(
            "SELECT processing_status FROM documents WHERE document_url=$1", PDF_URL
        ) == "error"

    # 2ª rodada: downloader funcional → deve processar
    svc_ok = _make_service(pool_e2e)
    stats = await svc_ok.reprocess_errors()

    assert stats.processed == 1

    async with pool_e2e.acquire() as conn:
        final_status = await conn.fetchval(
            "SELECT processing_status FROM documents WHERE document_url=$1", PDF_URL
        )
    assert final_status == "done"
