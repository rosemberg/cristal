"""Etapa 3 — TDD: Domain entities + value objects.

RED phase: testes escritos antes das implementações.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document import Document
from app.domain.entities.document_table import DocumentTable
from app.domain.entities.page import Page
from app.domain.entities.session import ChatSession
from app.domain.value_objects.chat_message import ChatMessage, Citation, TableData
from app.domain.value_objects.intent import QueryIntent
from app.domain.value_objects.search_result import ChunkMatch, HybridSearchResult, PageMatch

# ── helpers ────────────────────────────────────────────────────────────────────


def make_page(**kwargs) -> Page:
    defaults: dict = {
        "id": 1,
        "url": "https://www.tre-pi.jus.br/transparencia",
        "title": "Transparência",
        "content_type": "page",
        "depth": 0,
    }
    defaults.update(kwargs)
    return Page(**defaults)


def make_chunk(**kwargs) -> DocumentChunk:
    defaults: dict = {
        "id": 1,
        "document_url": "https://www.tre-pi.jus.br/doc.pdf",
        "chunk_index": 0,
        "text": "Conteúdo do chunk de teste.",
        "token_count": 10,
    }
    defaults.update(kwargs)
    return DocumentChunk(**defaults)


def make_citation(**kwargs) -> Citation:
    defaults: dict = {
        "document_title": "Relatório de Transparência",
        "document_url": "https://www.tre-pi.jus.br/relatorio.pdf",
        "page_number": 3,
        "snippet": "Texto relevante extraído do documento.",
    }
    defaults.update(kwargs)
    return Citation(**defaults)


# ── Page entity ────────────────────────────────────────────────────────────────


def test_page_entity_creation():
    page = make_page()

    assert page.id == 1
    assert page.url == "https://www.tre-pi.jus.br/transparencia"
    assert page.title == "Transparência"
    assert page.content_type == "page"
    assert page.depth == 0
    assert page.description is None
    assert page.tags == []
    assert page.documents == []


def test_page_entity_rejects_empty_url():
    with pytest.raises(ValueError, match="URL"):
        make_page(url="")


def test_page_entity_rejects_invalid_content_type():
    with pytest.raises(ValueError, match="content_type"):
        make_page(content_type="html")


def test_page_entity_accepts_all_valid_content_types():
    for ct in ("page", "pdf", "csv", "video", "api"):
        page = make_page(content_type=ct)
        assert page.content_type == ct


def test_page_entity_stores_optional_fields():
    page = make_page(
        description="Portal de Transparência",
        category="institucional",
        subcategory="financeiro",
        parent_url="https://www.tre-pi.jus.br",
        tags=["licitação", "contratos"],
    )

    assert page.description == "Portal de Transparência"
    assert page.category == "institucional"
    assert page.subcategory == "financeiro"
    assert page.tags == ["licitação", "contratos"]


# ── Document entity ────────────────────────────────────────────────────────────


def test_document_entity_with_chunks():
    chunk = make_chunk()
    doc = Document(
        id=1,
        page_url="https://www.tre-pi.jus.br/transparencia",
        document_url="https://www.tre-pi.jus.br/doc.pdf",
        type="pdf",
        is_processed=True,
        chunks=[chunk],
    )

    assert doc.id == 1
    assert doc.type == "pdf"
    assert doc.is_processed is True
    assert len(doc.chunks) == 1
    assert doc.chunks[0].text == "Conteúdo do chunk de teste."


def test_document_entity_defaults():
    doc = Document(
        id=2,
        page_url="https://www.tre-pi.jus.br/transparencia",
        document_url="https://www.tre-pi.jus.br/planilha.csv",
        type="csv",
    )

    assert doc.is_processed is False
    assert doc.title is None
    assert doc.num_pages is None
    assert doc.chunks == []
    assert doc.tables == []


# ── DocumentChunk entity ───────────────────────────────────────────────────────


def test_chunk_entity_stores_fields():
    chunk = make_chunk(
        section_title="Seção 1 — Introdução",
        page_number=2,
        token_count=42,
    )

    assert chunk.section_title == "Seção 1 — Introdução"
    assert chunk.page_number == 2
    assert chunk.token_count == 42


# ── DocumentTable entity ───────────────────────────────────────────────────────


def test_document_table_entity_stores_fields():
    table = DocumentTable(
        id=1,
        document_url="https://www.tre-pi.jus.br/doc.pdf",
        table_index=0,
        headers=["Nome", "Valor", "Data"],
        rows=[["TRE-PI", "1000,00", "2024-01-01"]],
        page_number=5,
        caption="Tabela de despesas",
        num_rows=1,
        num_cols=3,
    )

    assert table.headers == ["Nome", "Valor", "Data"]
    assert len(table.rows) == 1
    assert table.caption == "Tabela de despesas"


# ── ChatSession entity ─────────────────────────────────────────────────────────


def test_session_entity_message_append():
    session = ChatSession(
        id=uuid4(),
        created_at=datetime.now(UTC),
        last_active=datetime.now(UTC),
    )
    message = ChatMessage(role="user", content="Olá!", sources=[], tables=[])
    old_last_active = session.last_active

    session.add_message(message)

    assert len(session.messages) == 1
    assert session.messages[0].content == "Olá!"
    assert session.last_active >= old_last_active


def test_session_limits_history_to_max():
    session = ChatSession(
        id=uuid4(),
        created_at=datetime.now(UTC),
        last_active=datetime.now(UTC),
    )
    for i in range(15):
        session.add_message(
            ChatMessage(role="user", content=f"msg {i}", sources=[], tables=[]),
            max_history=10,
        )

    assert len(session.messages) == 10
    # deve manter as 10 mais recentes
    assert session.messages[0].content == "msg 5"
    assert session.messages[-1].content == "msg 14"


def test_session_entity_defaults():
    session = ChatSession(
        id=uuid4(),
        created_at=datetime.now(UTC),
        last_active=datetime.now(UTC),
    )

    assert session.title is None
    assert session.messages == []
    assert session.documents_consulted == []


# ── QueryIntent value object ───────────────────────────────────────────────────


def test_query_intent_classification():
    assert QueryIntent.GENERAL_SEARCH == "busca_geral"
    assert QueryIntent.DOCUMENT_QUERY == "consulta_documento"
    assert QueryIntent.DATA_QUERY == "consulta_dados"
    assert QueryIntent.NAVIGATION == "navegacao"
    assert QueryIntent.FOLLOWUP == "followup"


def test_query_intent_is_string_enum():
    intent = QueryIntent.GENERAL_SEARCH
    assert isinstance(intent, str)
    assert intent == "busca_geral"


# ── Citation value object (imutável) ──────────────────────────────────────────


def test_citation_value_object_immutable():
    citation = make_citation()

    with pytest.raises(FrozenInstanceError):
        citation.page_number = 99  # type: ignore[misc]


def test_citation_value_object_equality():
    c1 = make_citation()
    c2 = make_citation()

    assert c1 == c2


# ── ChatMessage value object ───────────────────────────────────────────────────


def test_chat_message_immutable():
    msg = ChatMessage(role="assistant", content="Resposta.", sources=[], tables=[])

    with pytest.raises(FrozenInstanceError):
        msg.content = "outro"  # type: ignore[misc]


def test_chat_message_with_citation():
    citation = make_citation()
    msg = ChatMessage(
        role="assistant",
        content="Veja o relatório.",
        sources=[citation],
        tables=[],
    )

    assert len(msg.sources) == 1
    assert msg.sources[0].document_title == "Relatório de Transparência"


# ── TableData value object ─────────────────────────────────────────────────────


def test_table_data_immutable():
    table = TableData(
        title="Despesas",
        headers=["Item", "Valor"],
        rows=[["Diárias", "500,00"]],
        source_document="https://www.tre-pi.jus.br/doc.pdf",
        page_number=1,
    )

    with pytest.raises(FrozenInstanceError):
        table.title = "Outro"  # type: ignore[misc]


# ── Search result value objects ────────────────────────────────────────────────


def test_page_match_immutable():
    page = make_page()
    match = PageMatch(page=page, score=0.85, highlight="texto relevante")

    with pytest.raises(FrozenInstanceError):
        match.score = 0.5  # type: ignore[misc]


def test_chunk_match_stores_fields():
    chunk = make_chunk()
    match = ChunkMatch(
        chunk=chunk,
        document_title="Relatório Anual",
        document_url="https://www.tre-pi.jus.br/relatorio.pdf",
        score=0.92,
    )

    assert match.score == 0.92
    assert match.document_title == "Relatório Anual"


def test_hybrid_search_result_aggregates():
    page = make_page()
    chunk = make_chunk()
    result = HybridSearchResult(
        pages=[PageMatch(page=page, score=0.9, highlight=None)],
        chunks=[ChunkMatch(chunk=chunk, document_title="Doc", document_url="url", score=0.8)],
        tables=[],
    )

    assert len(result.pages) == 1
    assert len(result.chunks) == 1
    assert result.tables == []
