"""Shared pytest fixtures for all test levels."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import UUID

import pytest

from app.domain.entities.chunk import DocumentChunk
from app.domain.entities.document import Document
from app.domain.entities.document_table import DocumentTable
from app.domain.entities.page import Page
from app.domain.entities.session import ChatSession
from app.domain.ports.outbound.analytics_repository import AnalyticsRepository
from app.domain.ports.outbound.content_fetch_gateway import (
    ContentFetchGateway,
    FetchResult,
)
from app.domain.ports.outbound.document_repository import (
    DocumentRepository,
    ProcessedDocument,
)
from app.domain.ports.outbound.document_process_gateway import DocumentProcessGateway
from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.ports.outbound.search_repository import SearchRepository
from app.domain.ports.outbound.session_repository import SessionRepository
from app.domain.value_objects.chat_message import ChatMessage
from app.domain.value_objects.search_result import (
    ChunkMatch,
    PageMatch,
)

# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def test_settings(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Settings configurado para testes unitários (sem banco real)."""
    monkeypatch.setenv("CRISTAL_VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setenv(
        "CRISTAL_DATABASE_URL",
        "postgresql+asyncpg://cristal:cristal@localhost:5432/cristal_test",
    )
    from importlib import reload

    import app.config.settings as mod

    reload(mod)
    from app.config.settings import Settings

    return Settings(_env_file=None)  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Sample domain data
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_pages() -> list[Page]:
    """10 páginas de teste representativas do portal TRE-PI."""
    return [
        Page(
            id=i,
            url=f"https://www.tre-pi.jus.br/pagina-{i}",
            title=f"Página de teste {i}",
            content_type="page",
            depth=1,
            description=f"Descrição da página {i}",
            category="transparencia",
        )
        for i in range(1, 11)
    ]


@pytest.fixture
def sample_documents(sample_pages: list[Page]) -> list[Document]:
    """5 documentos com chunks para testes."""
    docs = []
    for i in range(1, 6):
        chunk = DocumentChunk(
            id=i * 10,
            document_url=f"https://www.tre-pi.jus.br/doc-{i}.pdf",
            chunk_index=0,
            text=f"Conteúdo do chunk {i} com informações relevantes.",
            token_count=50,
            section_title=f"Seção {i}",
            page_number=1,
        )
        table = DocumentTable(
            id=i * 100,
            document_url=f"https://www.tre-pi.jus.br/doc-{i}.pdf",
            table_index=0,
            headers=["Coluna A", "Coluna B"],
            rows=[["valor 1", "valor 2"]],
            caption=f"Tabela {i}",
            page_number=1,
        )
        doc = Document(
            id=i,
            page_url=sample_pages[i - 1].url,
            document_url=f"https://www.tre-pi.jus.br/doc-{i}.pdf",
            type="pdf",
            is_processed=True,
            title=f"Documento {i}",
            num_pages=5,
            chunks=[chunk],
            tables=[table],
        )
        docs.append(doc)
    return docs


# ---------------------------------------------------------------------------
# Fake implementations
# ---------------------------------------------------------------------------


class FakeSearchRepository(SearchRepository):
    """In-memory search repository for unit tests."""

    def __init__(self, pages: list[Page] | None = None) -> None:
        self._pages = pages or []

    async def search_pages(self, query: str, top_k: int = 5) -> list[PageMatch]:
        results = [
            PageMatch(page=p, score=1.0, highlight=query)
            for p in self._pages
            if query.lower() in p.title.lower()
        ]
        return results[:top_k]

    async def search_chunks(self, query: str, top_k: int = 5) -> list[ChunkMatch]:
        return []

    async def search_tables(self, query: str) -> list[DocumentTable]:
        return []

    async def get_categories(self) -> list[dict[str, object]]:
        return [{"name": "transparencia", "count": len(self._pages)}]

    async def get_stats(self) -> dict[str, object]:
        return {"total_pages": len(self._pages), "total_chunks": 0, "total_tables": 0}


class FakeDocumentRepository(DocumentRepository):
    """In-memory document repository for unit tests."""

    def __init__(self, documents: list[Document] | None = None) -> None:
        self._docs: dict[str, Document] = {
            d.document_url: d for d in (documents or [])
        }

    async def find_by_url(self, url: str) -> Document | None:
        return self._docs.get(url)

    async def list_documents(
        self,
        category: str | None = None,
        doc_type: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> list[Document]:
        docs = list(self._docs.values())
        if doc_type:
            docs = [d for d in docs if d.type == doc_type]
        start = (page - 1) * size
        return docs[start : start + size]

    async def get_chunks(self, document_url: str) -> list[DocumentChunk]:
        doc = self._docs.get(document_url)
        return doc.chunks if doc else []

    async def get_tables(self, document_url: str) -> list[DocumentTable]:
        doc = self._docs.get(document_url)
        return doc.tables if doc else []

    async def save_content(
        self, document_url: str, content: ProcessedDocument
    ) -> None:
        if document_url in self._docs:
            self._docs[document_url].chunks = content.chunks
            self._docs[document_url].tables = content.tables
            self._docs[document_url].is_processed = True


class FakeSessionRepository(SessionRepository):
    """In-memory session repository for unit tests."""

    def __init__(self) -> None:
        self._sessions: dict[UUID, ChatSession] = {}

    async def create(self, title: str | None = None) -> ChatSession:
        session = ChatSession(
            id=uuid.uuid4(),
            created_at=datetime.now(UTC),
            last_active=datetime.now(UTC),
            title=title,
        )
        self._sessions[session.id] = session
        return session

    async def get(self, session_id: UUID) -> ChatSession | None:
        return self._sessions.get(session_id)

    async def save_message(self, session_id: UUID, message: ChatMessage) -> None:
        session = self._sessions.get(session_id)
        if session:
            session.add_message(message)

    async def list_sessions(self, limit: int = 20) -> list[ChatSession]:
        sessions = sorted(
            self._sessions.values(), key=lambda s: s.last_active, reverse=True
        )
        return sessions[:limit]


class FakeAnalyticsRepository(AnalyticsRepository):
    """In-memory analytics repository for unit tests."""

    def __init__(self) -> None:
        self._queries: list[dict[str, object]] = []
        self._next_id = 1

    async def log_query(
        self,
        session_id: UUID | None,
        query: str,
        intent_type: str,
        pages_found: int,
        chunks_found: int,
        tables_found: int,
        response_time_ms: int,
    ) -> int:
        qid = self._next_id
        self._next_id += 1
        self._queries.append(
            {
                "id": qid,
                "session_id": session_id,
                "query": query,
                "intent_type": intent_type,
                "pages_found": pages_found,
                "chunks_found": chunks_found,
                "tables_found": tables_found,
                "response_time_ms": response_time_ms,
                "feedback": None,
            }
        )
        return qid

    async def update_feedback(self, query_id: int, feedback: str) -> None:
        for q in self._queries:
            if q["id"] == query_id:
                q["feedback"] = feedback
                break

    async def get_metrics(self, days: int = 30) -> dict[str, object]:
        total = len(self._queries)
        avg: float = 0.0
        if total:
            total_ms: float = 0.0
            for q in self._queries:
                total_ms += float(q["response_time_ms"])  # type: ignore[arg-type]
            avg = total_ms / total
        return {"total_queries": total, "avg_response_time_ms": avg}

    async def get_daily_stats(self, days: int = 30) -> list[dict[str, object]]:
        return []


class FakeLLMGateway(LLMGateway):
    """Fixed-response LLM gateway for unit tests."""

    DEFAULT_RESPONSE = (
        '{"text": "Resposta de teste.", "sources": [], "tables": [], '
        '"suggestions": ["Saiba mais", "Ver documentos"]}'
    )

    def __init__(self, response: str | None = None) -> None:
        self._response = response or self.DEFAULT_RESPONSE

    async def generate(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
        temperature: float = 0.3,
    ) -> str:
        return self._response

    async def generate_stream(
        self,
        system_prompt: str,
        messages: list[dict[str, object]],
    ) -> AsyncIterator[str]:
        async def _stream() -> AsyncIterator[str]:
            for token in self._response.split():
                yield token + " "

        return _stream()


class FakeContentFetchGateway(ContentFetchGateway):
    """Fixed-content HTTP gateway for unit tests."""

    def __init__(self, content: str = "<p>Conteúdo de teste</p>") -> None:
        self._content = content

    async def fetch(self, url: str) -> FetchResult:
        return FetchResult(
            url=url,
            content=self._content,
            status_code=200,
        )


class FakeDocumentProcessGateway(DocumentProcessGateway):
    """Fixed-result document processor for unit tests."""

    def __init__(self, result: ProcessedDocument | None = None) -> None:
        self._result = result

    async def process(
        self, url: str, content: bytes, doc_type: str
    ) -> ProcessedDocument:
        if self._result is not None:
            return self._result
        return ProcessedDocument(
            document_url=url,
            text="Conteúdo extraído de teste.",
            chunks=[
                DocumentChunk(
                    id=0,
                    document_url=url,
                    chunk_index=0,
                    text="Conteúdo extraído de teste.",
                    token_count=5,
                )
            ],
            tables=[],
            num_pages=1,
            title=None,
        )


# ---------------------------------------------------------------------------
# Fixtures that expose fakes
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_search_repo(sample_pages: list[Page]) -> FakeSearchRepository:
    return FakeSearchRepository(pages=sample_pages)


@pytest.fixture
def sample_document_repo(sample_documents: list[Document]) -> FakeDocumentRepository:
    return FakeDocumentRepository(documents=sample_documents)


@pytest.fixture
def mock_llm_gateway() -> FakeLLMGateway:
    return FakeLLMGateway()


@pytest.fixture
def mock_content_fetcher() -> FakeContentFetchGateway:
    return FakeContentFetchGateway()


@pytest.fixture
def fake_document_processor() -> FakeDocumentProcessGateway:
    return FakeDocumentProcessGateway()


@pytest.fixture
def fake_session_repo() -> FakeSessionRepository:
    return FakeSessionRepository()


@pytest.fixture
def fake_analytics_repo() -> FakeAnalyticsRepository:
    return FakeAnalyticsRepository()
