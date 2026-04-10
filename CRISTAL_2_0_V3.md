# Cristal 2.0 — Plano de Evolução V3
## Transparência Inteligente com Arquitetura Hexagonal, TDD e PostgreSQL

---

## 1. Diagnóstico do Estado Atual

### O que funciona
- 890 páginas indexadas, 143 documentos catalogados, 36 categorias
- Busca full-text (FTS5/SQLite), chat com Gemini via Vertex AI
- Frontend funcional com cards, links, accordions, sugestões

### O que precisa mudar

| Problema | Impacto |
|----------|---------|
| Documentos (PDFs, CSVs) são apenas links — conteúdo nunca lido | Cidadão não obtém respostas sobre conteúdo documental |
| SQLite single-file, sem concorrência | Não escala, não suporta múltiplas réplicas |
| Zero testes | Sem garantia de regressão, refatoração arriscada |
| Acoplamento direto entre camadas | ChatEngine instancia dependências diretamente |
| Crawler tem `KnowledgeDB` duplicada (978 linhas separadas) | Dois schemas divergentes |
| Frontend básico | Não apresenta dados estruturados, sem histórico, sem navegação |
| Config via `os.getenv` espalhado | Sem validação, sem defaults tipados |

---

## 2. Princípios da Nova Arquitetura

### 2.1 Arquitetura Hexagonal (Ports & Adapters)

```
                    ┌─────────────────────────────────┐
                    │         DRIVING ADAPTERS         │
                    │   (quem inicia a ação)           │
                    │                                  │
                    │  FastAPI Router  │  CLI Crawler   │
                    └────────┬────────┴───────┬────────┘
                             │                │
                    ┌────────▼────────────────▼────────┐
                    │           INPUT PORTS             │
                    │                                   │
                    │  ChatUseCase    SearchUseCase      │
                    │  DocumentUseCase SessionUseCase    │
                    │  AnalyticsUseCase                  │
                    ├───────────────────────────────────┤
                    │           DOMAIN CORE              │
                    │                                   │
                    │  Entities: Page, Document, Chunk   │
                    │  Value Objects: SearchResult,      │
                    │    ChatMessage, Citation, Table    │
                    │  Services: ChatService,            │
                    │    SearchService, DocumentService  │
                    │    PromptBuilder                   │
                    ├───────────────────────────────────┤
                    │          OUTPUT PORTS              │
                    │                                   │
                    │  SearchRepository (ABC)            │
                    │  DocumentRepository (ABC)          │
                    │  SessionRepository (ABC)           │
                    │  LLMGateway (ABC)                  │
                    │  ContentFetchGateway (ABC)         │
                    │  AnalyticsRepository (ABC)         │
                    └────────┬────────────────┬─────────┘
                             │                │
                    ┌────────▼────────────────▼────────┐
                    │        DRIVEN ADAPTERS            │
                    │   (quem é chamado pelo domínio)   │
                    │                                   │
                    │  PostgresSearchRepo               │
                    │  PostgresDocumentRepo              │
                    │  PostgresSessionRepo               │
                    │  PostgresAnalyticsRepo             │
                    │  VertexAIGateway                   │
                    │  HttpContentFetcher                │
                    └───────────────────────────────────┘
```

### 2.2 SOLID

| Princípio | Aplicação |
|-----------|-----------|
| **S** — Single Responsibility | Cada service/adapter faz uma coisa. ChatService orquestra, PromptBuilder monta prompts, não parseia JSON nem formata HTML |
| **O** — Open/Closed | Ports (ABC) permitem trocar adapter sem alterar domínio (ex: trocar Gemini por GPT) |
| **L** — Liskov Substitution | Qualquer implementação de `LLMGateway` é intercambiável |
| **I** — Interface Segregation | Ports pequenos: `SearchRepository` não expõe métodos de escrita; `DocumentWriteRepository` separado |
| **D** — Dependency Inversion | Domain depende de abstrações (ABCs), nunca de PostgreSQL ou Vertex AI diretamente |

### 2.3 TDD

- **Red → Green → Refactor** em cada módulo
- Testes escritos ANTES da implementação de cada feature
- 3 níveis: Unit (domain puro), Integration (adapters com banco real), E2E (API completa)
- Coverage mínimo: 80% no domain, 70% nos adapters

---

## 3. Nova Estrutura de Diretórios

```
cristal/
├── app/
│   ├── domain/                          # CORE — zero dependências externas
│   │   ├── entities/
│   │   │   ├── __init__.py
│   │   │   ├── page.py                  # Page entity
│   │   │   ├── document.py              # Document entity
│   │   │   ├── chunk.py                 # DocumentChunk entity
│   │   │   └── session.py               # ChatSession entity
│   │   ├── value_objects/
│   │   │   ├── __init__.py
│   │   │   ├── search_result.py         # SearchResult, DocumentMatch
│   │   │   ├── chat_message.py          # ChatMessage, Citation, TableData
│   │   │   └── intent.py               # QueryIntent enum
│   │   ├── services/
│   │   │   ├── __init__.py
│   │   │   ├── chat_service.py          # Orquestração: search → fetch → LLM → response
│   │   │   ├── search_service.py        # Busca híbrida (páginas + chunks + tabelas)
│   │   │   ├── document_service.py      # Processamento e consulta de documentos
│   │   │   └── prompt_builder.py        # Montagem de system prompt com contexto dinâmico
│   │   └── ports/
│   │       ├── __init__.py
│   │       ├── inbound/
│   │       │   ├── __init__.py
│   │       │   ├── chat_use_case.py     # ABC: process_message, get_suggestions
│   │       │   ├── search_use_case.py   # ABC: search_pages, search_documents
│   │       │   ├── document_use_case.py # ABC: list, get, get_content, get_tables
│   │       │   └── session_use_case.py  # ABC: create, get, list_messages
│   │       └── outbound/
│   │           ├── __init__.py
│   │           ├── search_repository.py     # ABC: search, get_categories, get_stats
│   │           ├── document_repository.py   # ABC: find, get_chunks, get_tables, save
│   │           ├── session_repository.py    # ABC: create, get, save_message, list
│   │           ├── llm_gateway.py           # ABC: generate(prompt, messages) → str
│   │           ├── content_fetch_gateway.py # ABC: fetch(url) → FetchResult
│   │           └── analytics_repository.py  # ABC: log_query, get_metrics, get_daily_stats
│   │
│   ├── adapters/                        # Implementações concretas
│   │   ├── inbound/
│   │   │   ├── __init__.py
│   │   │   ├── fastapi/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── app.py              # FastAPI app factory
│   │   │   │   ├── dependencies.py     # Depends() com injeção de ports
│   │   │   │   ├── chat_router.py      # POST /api/chat, GET /api/suggest
│   │   │   │   ├── document_router.py  # GET /api/documents/...
│   │   │   │   ├── session_router.py   # POST/GET /api/sessions/...
│   │   │   │   ├── map_router.py       # GET /api/transparency-map
│   │   │   │   ├── analytics_router.py # GET /api/admin/analytics
│   │   │   │   ├── health_router.py    # GET /api/health (evoluído)
│   │   │   │   └── schemas.py          # Pydantic request/response models
│   │   │   └── cli/
│   │   │       ├── __init__.py
│   │   │       └── crawler.py          # CLI crawler (usa mesmos ports)
│   │   └── outbound/
│   │       ├── __init__.py
│   │       ├── postgres/
│   │       │   ├── __init__.py
│   │       │   ├── connection.py       # AsyncPG pool + migrations
│   │       │   ├── search_repo.py      # PostgresSearchRepository
│   │       │   ├── document_repo.py    # PostgresDocumentRepository
│   │       │   ├── session_repo.py     # PostgresSessionRepository
│   │       │   └── analytics_repo.py   # PostgresAnalyticsRepository
│   │       ├── vertex_ai/
│   │       │   ├── __init__.py
│   │       │   └── gateway.py          # VertexAIGateway implements LLMGateway
│   │       ├── http/
│   │       │   ├── __init__.py
│   │       │   └── content_fetcher.py  # HttpContentFetcher implements ContentFetchGateway
│   │       └── document_processor/
│   │           ├── __init__.py
│   │           ├── pdf_processor.py    # PyMuPDF: extrai texto + tabelas
│   │           ├── csv_processor.py    # Pandas: lê CSV/XLSX
│   │           └── chunker.py          # Chunking com overlap
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py                # Pydantic Settings v2 (validado, tipado)
│   │
│   └── main.py                         # Entrypoint: cria app, injeta dependências
│
├── migrations/                          # Alembic migrations para PostgreSQL
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
│       └── 001_initial_schema.py
│
├── scripts/
│   ├── crawler.py                      # Refatorado: usa adapters/inbound/cli/
│   └── seed_db.py                      # Migrar dados do SQLite → PostgreSQL
│
├── tests/
│   ├── conftest.py                     # Fixtures compartilhadas
│   ├── unit/
│   │   ├── domain/
│   │   │   ├── test_chat_service.py
│   │   │   ├── test_search_service.py
│   │   │   ├── test_document_service.py
│   │   │   ├── test_prompt_builder.py
│   │   │   └── test_entities.py
│   │   └── adapters/
│   │       ├── test_vertex_gateway.py
│   │       ├── test_content_fetcher.py
│   │       └── test_document_processor.py
│   ├── integration/
│   │   ├── conftest.py                # PostgreSQL via testcontainers
│   │   ├── test_postgres_search_repo.py
│   │   ├── test_postgres_document_repo.py
│   │   ├── test_postgres_session_repo.py
│   │   ├── test_chat_api.py
│   │   ├── test_document_api.py
│   │   ├── test_session_api.py
│   │   └── test_crawler.py
│   └── fixtures/
│       ├── sample_pages.json
│       ├── sample_documents.json
│       ├── sample_pdf_small.pdf
│       ├── sample_csv.csv
│       └── mock_llm_responses.json
│
├── static/                              # Frontend evoluído
│   ├── index.html
│   ├── css/
│   │   ├── style.css                   # Reset + design system
│   │   ├── components.css              # Cards, tabelas, sidebar
│   │   └── animations.css              # Transições, loading states
│   ├── js/
│   │   ├── app.js                      # Inicialização + router (SPA-like)
│   │   ├── api.js                      # Módulo de chamadas API
│   │   ├── chat.js                     # Componente de chat
│   │   ├── documents.js                # Componente de documentos
│   │   ├── map.js                      # Mapa de transparência
│   │   ├── sessions.js                 # Gerenciamento de sessões
│   │   └── utils.js                    # Markdown, tabelas, formatação
│   └── assets/
│       └── icons/                       # SVGs inline para tipos de documento
│
├── docker-compose.yml                   # PostgreSQL + app para dev
├── Containerfile                        # Produção (OpenShift)
├── requirements.txt                     # Dependências de produção
├── requirements-dev.txt                 # pytest, testcontainers, ruff, mypy
├── pyproject.toml                       # Configuração do projeto
└── alembic.ini                          # Config do Alembic
```

---

## 4. PostgreSQL — Schema Completo

### 4.1 Schema

```sql
-- ====================================================
-- EXTENSÕES
-- ====================================================

CREATE EXTENSION IF NOT EXISTS pg_trgm;  -- busca por similaridade

-- ====================================================
-- SCHEMA: knowledge (páginas e documentos)
-- ====================================================

-- Páginas do portal de transparência
CREATE TABLE pages (
    id SERIAL PRIMARY KEY,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    main_content TEXT,
    content_summary TEXT,
    category TEXT,
    subcategory TEXT,
    content_type TEXT DEFAULT 'page',       -- page, pdf, csv, video, api
    depth INTEGER DEFAULT 0,
    parent_url TEXT,
    breadcrumb JSONB DEFAULT '[]',
    tags TEXT[] DEFAULT '{}',
    search_vector TSVECTOR,                 -- busca full-text nativa do PG
    last_modified TIMESTAMPTZ,
    extracted_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_pages_category ON pages(category);
CREATE INDEX idx_pages_content_type ON pages(content_type);
CREATE INDEX idx_pages_search ON pages USING GIN(search_vector);
CREATE INDEX idx_pages_tags ON pages USING GIN(tags);
CREATE INDEX idx_pages_trgm_title ON pages USING GIN(title gin_trgm_ops);

-- Trigger para atualizar search_vector automaticamente
CREATE OR REPLACE FUNCTION pages_search_trigger() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('portuguese', COALESCE(NEW.title, '')), 'A') ||
        setweight(to_tsvector('portuguese', COALESCE(NEW.description, '')), 'B') ||
        setweight(to_tsvector('portuguese', COALESCE(NEW.category, '')), 'B') ||
        setweight(to_tsvector('portuguese', COALESCE(NEW.main_content, '')), 'C') ||
        setweight(to_tsvector('portuguese', COALESCE(array_to_string(NEW.tags, ' '), '')), 'B');
    NEW.updated_at := NOW();
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER pages_search_update
    BEFORE INSERT OR UPDATE ON pages
    FOR EACH ROW EXECUTE FUNCTION pages_search_trigger();

-- Documentos referenciados (PDFs, CSVs, etc.)
CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    page_url TEXT NOT NULL REFERENCES pages(url) ON DELETE CASCADE,
    document_url TEXT NOT NULL,
    document_title TEXT,
    document_type TEXT,                     -- pdf, csv, xlsx, doc
    context TEXT,                           -- texto ao redor do link na página
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(page_url, document_url)
);

CREATE INDEX idx_documents_page ON documents(page_url);
CREATE INDEX idx_documents_type ON documents(document_type);

-- Links entre páginas
CREATE TABLE page_links (
    id SERIAL PRIMARY KEY,
    source_url TEXT NOT NULL REFERENCES pages(url) ON DELETE CASCADE,
    target_url TEXT NOT NULL,
    link_title TEXT,
    link_type TEXT DEFAULT 'internal',      -- internal, external
    UNIQUE(source_url, target_url)
);

CREATE INDEX idx_page_links_source ON page_links(source_url);

-- Árvore de navegação
CREATE TABLE navigation_tree (
    id SERIAL PRIMARY KEY,
    parent_url TEXT NOT NULL,
    child_url TEXT NOT NULL,
    child_title TEXT,
    sort_order INTEGER DEFAULT 0,
    UNIQUE(parent_url, child_url)
);

CREATE INDEX idx_nav_parent ON navigation_tree(parent_url);

-- ====================================================
-- SCHEMA: document_content (conteúdo extraído)
-- ====================================================

-- Conteúdo completo extraído de documentos
CREATE TABLE document_contents (
    id SERIAL PRIMARY KEY,
    document_url TEXT NOT NULL,
    page_url TEXT REFERENCES pages(url) ON DELETE SET NULL,
    document_title TEXT,
    document_type TEXT,                     -- pdf, csv, xlsx
    full_text TEXT,
    num_pages INTEGER,
    file_size_bytes BIGINT,
    processing_status TEXT DEFAULT 'pending',  -- pending, processing, done, error
    error_message TEXT,
    extracted_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(document_url)
);

-- Chunks para RAG (Retrieval-Augmented Generation)
CREATE TABLE document_chunks (
    id SERIAL PRIMARY KEY,
    document_url TEXT NOT NULL REFERENCES document_contents(document_url) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    section_title TEXT,
    page_number INTEGER,
    token_count INTEGER,
    search_vector TSVECTOR,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_chunks_document ON document_chunks(document_url);
CREATE INDEX idx_chunks_search ON document_chunks USING GIN(search_vector);

CREATE OR REPLACE FUNCTION chunks_search_trigger() RETURNS trigger AS $$
BEGIN
    NEW.search_vector :=
        setweight(to_tsvector('portuguese', COALESCE(NEW.section_title, '')), 'A') ||
        setweight(to_tsvector('portuguese', COALESCE(NEW.chunk_text, '')), 'B');
    RETURN NEW;
END
$$ LANGUAGE plpgsql;

CREATE TRIGGER chunks_search_update
    BEFORE INSERT OR UPDATE ON document_chunks
    FOR EACH ROW EXECUTE FUNCTION chunks_search_trigger();

-- Tabelas extraídas de documentos
CREATE TABLE document_tables (
    id SERIAL PRIMARY KEY,
    document_url TEXT NOT NULL REFERENCES document_contents(document_url) ON DELETE CASCADE,
    table_index INTEGER NOT NULL,
    page_number INTEGER,
    headers JSONB NOT NULL DEFAULT '[]',
    rows JSONB NOT NULL DEFAULT '[]',
    caption TEXT,
    num_rows INTEGER,
    num_cols INTEGER,
    search_text TEXT,                       -- headers + caption concatenados
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_tables_document ON document_tables(document_url);

-- ====================================================
-- SCHEMA: sessions (conversas persistentes)
-- ====================================================

CREATE TABLE chat_sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT,
    message_count INTEGER DEFAULT 0,
    documents_consulted TEXT[] DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_active TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_sessions_active ON chat_sessions(last_active DESC);

CREATE TABLE chat_messages (
    id SERIAL PRIMARY KEY,
    session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}',            -- links, sources, category, tables
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_messages_session ON chat_messages(session_id, created_at);

-- ====================================================
-- SCHEMA: analytics
-- ====================================================

CREATE TABLE query_logs (
    id SERIAL PRIMARY KEY,
    session_id UUID REFERENCES chat_sessions(id) ON DELETE SET NULL,
    query TEXT NOT NULL,
    intent_type TEXT,
    pages_found INTEGER DEFAULT 0,
    chunks_found INTEGER DEFAULT 0,
    tables_found INTEGER DEFAULT 0,
    response_time_ms INTEGER,
    feedback TEXT CHECK (feedback IN ('positive', 'negative', NULL)),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_logs_created ON query_logs(created_at DESC);
CREATE INDEX idx_logs_intent ON query_logs(intent_type);
CREATE INDEX idx_logs_created_date ON query_logs(DATE(created_at));

-- ====================================================
-- VIEWS úteis
-- ====================================================

-- Estatísticas gerais
CREATE OR REPLACE VIEW transparency_stats AS
SELECT
    (SELECT COUNT(*) FROM pages) AS total_pages,
    (SELECT COUNT(*) FROM documents) AS total_documents,
    (SELECT COUNT(DISTINCT category) FROM pages WHERE category IS NOT NULL) AS total_categories,
    (SELECT COUNT(*) FROM page_links) AS total_links,
    (SELECT COUNT(*) FROM document_contents WHERE processing_status = 'done') AS documents_processed,
    (SELECT COUNT(*) FROM document_chunks) AS total_chunks,
    (SELECT COUNT(*) FROM document_tables) AS total_tables,
    (SELECT COUNT(*) FROM chat_sessions) AS total_sessions;

-- Mapa de transparência hierárquico
CREATE OR REPLACE VIEW transparency_map AS
SELECT
    p.category,
    p.subcategory,
    COUNT(DISTINCT p.id) AS page_count,
    COUNT(DISTINCT d.id) AS document_count,
    ARRAY_AGG(DISTINCT p.content_type) FILTER (WHERE p.content_type IS NOT NULL) AS content_types
FROM pages p
LEFT JOIN documents d ON d.page_url = p.url
WHERE p.category IS NOT NULL
GROUP BY p.category, p.subcategory
ORDER BY p.category, p.subcategory;
```

### 4.2 Vantagens do PostgreSQL sobre SQLite

| Aspecto | SQLite (atual) | PostgreSQL (novo) |
|---------|---------------|-------------------|
| Concorrência | Single-writer | Multi-writer MVCC |
| Full-text search | FTS5 (sem dicionário PT) | `tsvector` com dicionário `portuguese` |
| Busca fuzzy | Nenhuma | `pg_trgm` (similaridade, typos) |
| JSON | Limitado | `JSONB` nativo com índices GIN |
| Escalabilidade | 1 réplica | N réplicas read-only |
| Migrações | Manual | Alembic com versionamento |
| Views | Limitado | Views materializadas para analytics |
| Arrays | Nenhum | `TEXT[]` nativo com índice GIN |
| Weights no FTS | Não | `setweight()` A/B/C/D por campo |

---

## 5. Domain Core — Entities e Value Objects

### 5.1 Entities

```python
# app/domain/entities/page.py
@dataclass
class Page:
    id: int
    url: str
    title: str
    description: str | None
    main_content: str | None
    content_summary: str | None
    category: str | None
    subcategory: str | None
    content_type: str  # page, pdf, csv, video, api
    depth: int
    parent_url: str | None
    breadcrumb: list[dict]
    tags: list[str]
    documents: list["Document"]  # agregação

    def __post_init__(self):
        if not self.url:
            raise ValueError("Page URL cannot be empty")
        if self.content_type not in ("page", "pdf", "csv", "video", "api"):
            raise ValueError(f"Invalid content_type: {self.content_type}")

# app/domain/entities/document.py
@dataclass
class Document:
    id: int
    page_url: str
    document_url: str
    title: str | None
    type: str  # pdf, csv, xlsx
    is_processed: bool
    num_pages: int | None
    chunks: list["DocumentChunk"]
    tables: list["DocumentTable"]

# app/domain/entities/chunk.py
@dataclass
class DocumentChunk:
    id: int
    document_url: str
    chunk_index: int
    text: str
    section_title: str | None
    page_number: int | None
    token_count: int

# app/domain/entities/session.py
@dataclass
class ChatSession:
    id: UUID
    title: str | None
    messages: list["ChatMessage"]
    documents_consulted: list[str]
    created_at: datetime
    last_active: datetime

    def add_message(self, message: "ChatMessage", max_history: int = 10) -> None:
        self.messages.append(message)
        if len(self.messages) > max_history:
            self.messages = self.messages[-max_history:]
        self.last_active = datetime.now(UTC)
```

### 5.2 Value Objects

```python
# app/domain/value_objects/search_result.py
@dataclass(frozen=True)
class PageMatch:
    page: Page
    score: float
    highlight: str | None

@dataclass(frozen=True)
class ChunkMatch:
    chunk: DocumentChunk
    document_title: str
    document_url: str
    score: float

@dataclass(frozen=True)
class HybridSearchResult:
    pages: list[PageMatch]
    chunks: list[ChunkMatch]
    tables: list["DocumentTable"]

# app/domain/value_objects/chat_message.py
@dataclass(frozen=True)
class ChatMessage:
    role: str  # user, assistant
    content: str
    sources: list["Citation"]
    tables: list["TableData"]

@dataclass(frozen=True)
class Citation:
    document_title: str
    document_url: str
    page_number: int | None
    snippet: str

@dataclass(frozen=True)
class TableData:
    title: str | None
    headers: list[str]
    rows: list[list[str]]
    source_document: str
    page_number: int | None

# app/domain/value_objects/intent.py
class QueryIntent(str, Enum):
    GENERAL_SEARCH = "busca_geral"
    DOCUMENT_QUERY = "consulta_documento"
    DATA_QUERY = "consulta_dados"
    NAVIGATION = "navegacao"
    FOLLOWUP = "followup"
```

### 5.3 Output Ports (ABCs)

```python
# app/domain/ports/outbound/search_repository.py
class SearchRepository(ABC):
    @abstractmethod
    async def search_pages(self, query: str, top_k: int = 5) -> list[PageMatch]: ...

    @abstractmethod
    async def search_chunks(self, query: str, top_k: int = 5) -> list[ChunkMatch]: ...

    @abstractmethod
    async def search_tables(self, query: str) -> list[DocumentTable]: ...

    @abstractmethod
    async def get_categories(self) -> list[dict]: ...

    @abstractmethod
    async def get_stats(self) -> dict: ...

# app/domain/ports/outbound/llm_gateway.py
class LLMGateway(ABC):
    @abstractmethod
    async def generate(self, system_prompt: str,
                       messages: list[dict],
                       temperature: float = 0.3) -> str: ...

    @abstractmethod
    async def generate_stream(self, system_prompt: str,
                              messages: list[dict]) -> AsyncIterator[str]: ...

# app/domain/ports/outbound/document_repository.py
class DocumentRepository(ABC):
    @abstractmethod
    async def find_by_url(self, url: str) -> Document | None: ...

    @abstractmethod
    async def list_documents(self, category: str | None = None,
                             doc_type: str | None = None,
                             page: int = 1, size: int = 20) -> list[Document]: ...

    @abstractmethod
    async def get_chunks(self, document_url: str) -> list[DocumentChunk]: ...

    @abstractmethod
    async def get_tables(self, document_url: str) -> list[DocumentTable]: ...

    @abstractmethod
    async def save_content(self, document_url: str, content: "ProcessedDocument") -> None: ...

# app/domain/ports/outbound/session_repository.py
class SessionRepository(ABC):
    @abstractmethod
    async def create(self, title: str | None = None) -> ChatSession: ...

    @abstractmethod
    async def get(self, session_id: UUID) -> ChatSession | None: ...

    @abstractmethod
    async def save_message(self, session_id: UUID, message: ChatMessage) -> None: ...

    @abstractmethod
    async def list_sessions(self, limit: int = 20) -> list[ChatSession]: ...

# app/domain/ports/outbound/analytics_repository.py
class AnalyticsRepository(ABC):
    @abstractmethod
    async def log_query(self, session_id: UUID | None, query: str,
                        intent_type: str, pages_found: int,
                        chunks_found: int, tables_found: int,
                        response_time_ms: int) -> int: ...

    @abstractmethod
    async def update_feedback(self, query_id: int, feedback: str) -> None: ...

    @abstractmethod
    async def get_metrics(self, days: int = 30) -> dict: ...

    @abstractmethod
    async def get_daily_stats(self, days: int = 30) -> list[dict]: ...
```

### 5.4 Input Ports (ABCs)

```python
# app/domain/ports/inbound/chat_use_case.py
class ChatUseCase(ABC):
    @abstractmethod
    async def process_message(self, message: str, session_id: UUID | None = None,
                              history: list[dict] | None = None) -> ChatMessage: ...

    @abstractmethod
    async def get_suggestions(self) -> list[str]: ...

# app/domain/ports/inbound/search_use_case.py
class SearchUseCase(ABC):
    @abstractmethod
    async def search_pages(self, query: str, top_k: int = 5) -> list[PageMatch]: ...

    @abstractmethod
    async def search_documents(self, query: str, top_k: int = 5) -> HybridSearchResult: ...

# app/domain/ports/inbound/document_use_case.py
class DocumentUseCase(ABC):
    @abstractmethod
    async def list_documents(self, category: str | None = None,
                             doc_type: str | None = None,
                             page: int = 1, size: int = 20) -> list[Document]: ...

    @abstractmethod
    async def get(self, document_url: str) -> Document | None: ...

    @abstractmethod
    async def get_content(self, document_url: str) -> str | None: ...

    @abstractmethod
    async def get_tables(self, document_url: str) -> list[DocumentTable]: ...

# app/domain/ports/inbound/session_use_case.py
class SessionUseCase(ABC):
    @abstractmethod
    async def create(self, title: str | None = None) -> ChatSession: ...

    @abstractmethod
    async def get(self, session_id: UUID) -> ChatSession | None: ...

    @abstractmethod
    async def list_messages(self, session_id: UUID) -> list[ChatMessage]: ...
```

---

## 6. Plano TDD — Testes Primeiro

### 6.1 Estratégia de Testes

```
tests/
├── conftest.py                         # Fixtures globais
│   ├── fixture: pg_pool               # Pool asyncpg com banco de teste
│   ├── fixture: settings              # Settings de teste
│   ├── fixture: sample_pages          # 10 páginas de teste
│   ├── fixture: sample_documents      # 5 documentos com chunks
│   ├── fixture: mock_llm_gateway      # LLMGateway fake (respostas fixas)
│   ├── fixture: mock_content_fetcher  # ContentFetchGateway fake
│   └── fixture: test_client           # httpx.AsyncClient com app de teste
│
├── unit/domain/                        # Testes do CORE — sem I/O
│   ├── test_entities.py               # 8+ testes
│   │   ├── test_page_entity_creation
│   │   ├── test_page_entity_rejects_empty_url
│   │   ├── test_page_entity_rejects_invalid_content_type
│   │   ├── test_document_entity_with_chunks
│   │   ├── test_session_entity_message_append
│   │   ├── test_session_limits_history_to_max
│   │   ├── test_query_intent_classification
│   │   └── test_citation_value_object_immutable
│   │
│   ├── test_search_service.py         # 10+ testes
│   │   ├── test_hybrid_search_merges_pages_and_chunks
│   │   ├── test_hybrid_search_deduplicates_results
│   │   ├── test_ranks_by_combined_score
│   │   ├── test_includes_table_matches
│   │   ├── test_handles_empty_query
│   │   ├── test_handles_no_results
│   │   ├── test_respects_top_k_limit
│   │   ├── test_boosts_exact_title_matches
│   │   ├── test_search_with_category_filter
│   │   └── test_search_with_document_type_filter
│   │
│   ├── test_prompt_builder.py         # 5+ testes
│   │   ├── test_builds_base_system_prompt
│   │   ├── test_injects_page_context
│   │   ├── test_injects_chunk_citations_with_source
│   │   ├── test_injects_table_data_formatted
│   │   └── test_limits_context_to_max_tokens
│   │
│   ├── test_chat_service.py           # 15+ testes
│   │   ├── test_classifies_general_search_intent
│   │   ├── test_classifies_document_query_intent
│   │   ├── test_classifies_data_query_intent
│   │   ├── test_builds_prompt_with_page_context
│   │   ├── test_builds_prompt_with_chunk_citations
│   │   ├── test_builds_prompt_with_table_data
│   │   ├── test_parses_valid_json_response
│   │   ├── test_parses_malformed_json_with_fallback
│   │   ├── test_extracts_citations_from_response
│   │   ├── test_limits_history_to_max_messages
│   │   ├── test_returns_fallback_on_empty_search
│   │   ├── test_returns_fallback_on_llm_error
│   │   ├── test_includes_document_sources_in_response
│   │   ├── test_includes_tables_in_response
│   │   └── test_generates_followup_suggestions
│   │
│   └── test_document_service.py       # 10+ testes
│       ├── test_processes_pdf_extracts_text
│       ├── test_processes_pdf_extracts_tables
│       ├── test_processes_csv_reads_data
│       ├── test_chunks_long_document_with_overlap
│       ├── test_chunk_preserves_section_context
│       ├── test_chunk_token_count_within_limit
│       ├── test_handles_empty_pdf
│       ├── test_handles_corrupt_file
│       ├── test_handles_password_protected_pdf
│       └── test_get_document_with_full_context
│
├── unit/adapters/                      # Testes de adapters com mocks
│   ├── test_vertex_gateway.py         # Mock do SDK Vertex
│   ├── test_content_fetcher.py        # Mock do httpx
│   └── test_document_processor.py     # Arquivos de teste reais (pequenos PDFs/CSVs)
│
├── integration/                        # Testes com banco real
│   ├── conftest.py                    # PostgreSQL via testcontainers
│   ├── test_migrations.py            # Testa upgrade/downgrade/idempotência
│   ├── test_postgres_search_repo.py   # Testa FTS, ranking, filtros, stemming
│   ├── test_postgres_document_repo.py # Testa CRUD de documentos + chunks
│   ├── test_postgres_session_repo.py  # Testa sessões + mensagens
│   ├── test_chat_api.py              # Testa endpoints completos
│   ├── test_document_api.py          # Testa endpoints de documentos
│   ├── test_session_api.py           # Testa endpoints de sessões
│   └── test_crawler.py               # Testa crawler com banco real
│
└── fixtures/
    ├── sample_pages.json              # 10 páginas reais do TRE-PI
    ├── sample_documents.json          # 5 documentos com metadados
    ├── sample_pdf_small.pdf           # PDF de teste (2 páginas)
    ├── sample_csv.csv                 # CSV de teste
    └── mock_llm_responses.json        # Respostas esperadas do LLM
```

### 6.2 Dependências de Teste

```
# requirements-dev.txt
pytest>=8.0.0
pytest-asyncio>=0.24.0
pytest-cov>=5.0.0
testcontainers[postgres]>=4.0.0
httpx>=0.27.0                          # AsyncClient para testes de API
factory-boy>=3.3.0                     # Factories para entities
ruff>=0.8.0                            # Linting
mypy>=1.13.0                           # Type checking
```

### 6.3 Ciclo TDD por Feature

```
Para cada feature (ex: "busca híbrida"):

1. RED — Escrever teste que falha:
   test_hybrid_search_merges_pages_and_chunks()
   → assert len(result.pages) > 0
   → assert len(result.chunks) > 0

2. GREEN — Implementar o mínimo para passar:
   SearchService.hybrid_search() → chama search_repo.search_pages() + search_chunks()

3. REFACTOR — Limpar sem quebrar testes:
   Extrair ranking, deduplicação, etc.

4. Repetir para próximo cenário de teste
```

---

## 7. Frontend Profissional

### 7.1 Design System

**Paleta expandida:**
```css
:root {
    /* Primárias */
    --color-primary-900: #004D40;
    --color-primary-700: #006B5F;      /* atual */
    --color-primary-500: #00897B;      /* accent atual */
    --color-primary-300: #4DB6AC;
    --color-primary-100: #B2DFDB;
    --color-primary-50:  #E0F2F1;

    /* Neutras */
    --color-gray-900: #1A1A2E;
    --color-gray-700: #374151;
    --color-gray-500: #6B7280;
    --color-gray-300: #D1D5DB;
    --color-gray-100: #F3F4F6;
    --color-white:    #FFFFFF;

    /* Semânticas */
    --color-success: #059669;
    --color-warning: #D97706;
    --color-error:   #DC2626;
    --color-info:    #2563EB;

    /* Tipos de documento */
    --color-pdf:  #DC2626;
    --color-csv:  #059669;
    --color-xlsx: #2563EB;
    --color-doc:  #7C3AED;

    /* Espaçamento */
    --space-xs: 4px;
    --space-sm: 8px;
    --space-md: 16px;
    --space-lg: 24px;
    --space-xl: 32px;
    --space-2xl: 48px;

    /* Tipografia */
    --font-sans: 'Inter', system-ui, sans-serif;
    --font-mono: 'JetBrains Mono', monospace;
    --text-xs: 0.75rem;
    --text-sm: 0.875rem;
    --text-base: 1rem;
    --text-lg: 1.125rem;
    --text-xl: 1.25rem;
    --text-2xl: 1.5rem;

    /* Sombras */
    --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
    --shadow-md: 0 4px 6px rgba(0,0,0,0.07);
    --shadow-lg: 0 10px 15px rgba(0,0,0,0.1);

    /* Bordas */
    --radius-sm: 6px;
    --radius-md: 10px;
    --radius-lg: 16px;
    --radius-full: 9999px;
}
```

### 7.2 Layout — 3 Painéis

```
┌──────────────────────────────────────────────────────────┐
│  HEADER: Logo TRE-PI │ Cristal 2.0 │ Status │ [Mapa]    │
├──────────┬───────────────────────────────┬───────────────┤
│          │                               │               │
│ SIDEBAR  │     CHAT PRINCIPAL            │ PAINEL DOCS   │
│          │                               │  (contextual) │
│ Sessões  │  [Mensagens]                  │               │
│ ────────│                               │  Documento X   │
│ > Conv 1 │  Bot: "De acordo com o        │  ──────────── │
│   Conv 2 │  Relatório de Gestão 2024     │  Páginas: 45  │
│   Conv 3 │  [Fonte: RG2024, p.12],       │  Tipo: PDF    │
│          │  o valor total..."            │               │
│ ────────│                               │  [Conteúdo]   │
│ Categorias│  [Tabela interativa]          │  [Tabelas]    │
│ ────────│                               │  [Download]   │
│ > Gestão │  Sugestões: [chip] [chip]     │               │
│ > Licita │                               │               │
│ > Pessoa │  ─────────────────────        │               │
│          │  [    Digite sua pergunta   ] │               │
│          │                               │               │
├──────────┴───────────────────────────────┴───────────────┤
│  FOOTER: TRE-PI │ Versão 2.0 │ Dados atualizados em...  │
└──────────────────────────────────────────────────────────┘

Mobile: Sidebar vira drawer, painel docs vira modal bottom-sheet
```

### 7.3 Componentes do Frontend

**A) Chat Message Card (evoluído)**
```
┌─────────────────────────────────────────────┐
│  Gestão Orçamentária              3 fontes  │
├─────────────────────────────────────────────┤
│                                             │
│  De acordo com o Relatório de Gestão        │
│  Fiscal do 2º Quadrimestre de 2024          │
│  [Fonte: RGF-2Q2024, p.12]¹, o total       │
│  de despesas com pessoal foi de             │
│  R$ 45.231.890,00.                          │
│                                             │
│  ¹ Clique para ver o trecho original        │
│                                             │
├─────────────────────────────────────────────┤
│  Tabela: Despesas por Categoria             │
│ ┌──────────────┬────────────┬──────────┐    │
│ │ Categoria    │ Valor (R$) │ % Total  │    │
│ ├──────────────┼────────────┼──────────┤    │
│ │ Pessoal      │ 45.231.890 │  62,3%   │    │
│ │ Custeio      │ 18.456.230 │  25,4%   │    │
│ │ Investimento │  8.912.100 │  12,3%   │    │
│ └──────────────┴────────────┴──────────┘    │
│                           [Ordenar ▼] [1/3] │
├─────────────────────────────────────────────┤
│  Documentos consultados                     │
│ ┌──────────────────────────────────────┐    │
│ │  Relatório de Gestão Fiscal 2024    │    │
│ │    45 páginas │ PDF │ [Ver] [Abrir]  │    │
│ └──────────────────────────────────────┘    │
│ ┌──────────────────────────────────────┐    │
│ │  Planilha Orçamentária 2024         │    │
│ │    12 abas │ XLSX │ [Ver] [Abrir]   │    │
│ └──────────────────────────────────────┘    │
├─────────────────────────────────────────────┤
│  Links úteis                                │
│  - Portal de Gestão Fiscal          [>]     │
│  - Prestação de Contas 2024         [>]     │
├─────────────────────────────────────────────┤
│ Perguntas relacionadas:                     │
│ [Detalhar despesas com pessoal]             │
│ [Comparar com ano anterior]                 │
│ [Ver limite prudencial]                     │
├─────────────────────────────────────────────┤
│ [+]  [-]  Copiar  Exportar                  │
└─────────────────────────────────────────────┘
```

**B) Mapa de Transparência**
```
┌─────────────────────────────────────────────┐
│  Mapa de Transparência                      │
│ 890 páginas │ 143 documentos │ 36 categorias│
├─────────────────────────────────────────────┤
│  Filtrar categorias...                      │
├─────────────────────────────────────────────┤
│ v Gestão de Pessoas (85 páginas, 23 docs)   │
│   |-- Remuneração de Servidores             │
│   |   |-- Folha de Pagamento Jan/2024       │
│   |   |-- Relatório Analítico.pdf           │
│   |   +-- Dados Abertos.csv                 │
│   |-- Concursos e Seleções                  │
│   +-- Estrutura de Cargos                   │
│                                             │
│ > Licitações e Contratos (75 páginas)       │
│ > Gestão Orçamentária (62 páginas)          │
│ > Colegiados (126 páginas)                  │
│ ...                                         │
├─────────────────────────────────────────────┤
│ Click em qualquer item para iniciar chat    │
└─────────────────────────────────────────────┘
```

**C) Painel de Documento (contextual)**
```
┌─────────────────────────────────────────────┐
│  Relatório de Gestão Fiscal                 │
│ 2º Quadrimestre 2024                   [x]  │
├─────────────────────────────────────────────┤
│ Tipo: PDF │ 45 páginas │ 2.3 MB             │
│ Origem: Gestão Orçamentária e Financeira    │
│ Atualizado: 15/09/2024                      │
├─────────────────────────────────────────────┤
│ [Conteúdo] [Tabelas (8)] [Perguntar]        │
├─────────────────────────────────────────────┤
│                                             │
│ Seção: Demonstrativo da Despesa com Pessoal │
│ Página: 12                                  │
│ ─────────────────────────────────────────── │
│ "O total da despesa com pessoal atingiu     │
│ R$ 45.231.890,00 no período, representando  │
│ 42,3% da Receita Corrente Líquida,          │
│ abaixo do limite prudencial de 46,17%       │
│ estabelecido pela LRF."                     │
│                                             │
│ ─────────────────────────────────────────── │
│ Tabela 3.1 - Despesas por Natureza          │
│ ┌───────────────┬────────────┐              │
│ │ Natureza      │ Valor      │              │
│ ├───────────────┼────────────┤              │
│ │ Ativos        │ 32.150.000 │              │
│ │ Inativos      │  8.900.000 │              │
│ │ Pensionistas  │  4.181.890 │              │
│ └───────────────┴────────────┘              │
│                                             │
├─────────────────────────────────────────────┤
│ [Abrir PDF original]  [Baixar]              │
└─────────────────────────────────────────────┘
```

### 7.4 Responsividade

| Breakpoint | Layout |
|-----------|--------|
| `< 640px` (mobile) | Chat full-width, sidebar = hamburger drawer, painel docs = bottom sheet modal |
| `640-1024px` (tablet) | Chat + sidebar colapsável, painel docs = modal overlay |
| `> 1024px` (desktop) | 3 colunas: sidebar (250px) + chat (flex) + painel docs (350px, condicional) |

### 7.5 Modularização JS

```javascript
// js/app.js — Inicialização e estado global
const App = {
    state: { session: null, view: 'chat' },
    init() { /* bind modules, load session */ },
    navigate(view) { /* chat | map | document */ }
};

// js/api.js — Camada de comunicação
const API = {
    chat(message, sessionId, history) { /* POST /api/chat */ },
    suggest() { /* GET /api/suggest */ },
    getDocuments(filters) { /* GET /api/documents */ },
    getDocumentContent(id) { /* GET /api/documents/:id/content */ },
    getTransparencyMap() { /* GET /api/transparency-map */ },
    createSession() { /* POST /api/sessions */ },
    getSession(id) { /* GET /api/sessions/:id */ },
    sendFeedback(queryId, feedback) { /* POST /api/feedback */ }
};

// js/chat.js — Componente de chat (refatorado do atual)
// js/documents.js — Visualizador de documentos
// js/map.js — Mapa de transparência
// js/sessions.js — Gerenciamento de sessões
// js/utils.js — Markdown, tabelas, formatação (extraído do chat.js atual)
```

---

## 8. Configuração e Infraestrutura

### 8.1 Pydantic Settings v2

```python
# app/config/settings.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Banco de dados
    database_url: str = "postgresql+asyncpg://cristal:cristal@localhost:5432/cristal"
    db_pool_min: int = 2
    db_pool_max: int = 10

    # LLM
    vertex_project_id: str
    vertex_location: str = "us-central1"
    vertex_model: str = "gemini-2.5-flash-lite"
    google_application_credentials: str = ""

    # App
    allowed_origins: list[str] = ["*"]
    rate_limit_per_minute: int = 10
    max_history_messages: int = 10
    max_content_length: int = 5000      # aumentado de 3000

    # Cache
    cache_ttl_seconds: int = 3600

    # Document processing
    chunk_size_tokens: int = 1000
    chunk_overlap_tokens: int = 200
    max_document_size_mb: int = 50

    model_config = {"env_file": ".env", "env_prefix": "CRISTAL_"}
```

### 8.2 Docker Compose (desenvolvimento)

```yaml
# docker-compose.yml
services:
  db:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: cristal
      POSTGRES_USER: cristal
      POSTGRES_PASSWORD: cristal
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  app:
    build: .
    ports:
      - "8080:8080"
    environment:
      CRISTAL_DATABASE_URL: postgresql+asyncpg://cristal:cristal@db:5432/cristal
      CRISTAL_VERTEX_PROJECT_ID: ${VERTEX_PROJECT_ID}
      GOOGLE_APPLICATION_CREDENTIALS: /secrets/gcp-sa-key.json
    volumes:
      - ./secrets:/secrets:ro
    depends_on:
      - db

volumes:
  pgdata:
```

### 8.3 Dependências Atualizadas

```
# requirements.txt
fastapi>=0.115.0
uvicorn[standard]>=0.32.0
httpx>=0.27.0
beautifulsoup4>=4.12.0
lxml>=5.3.0
google-cloud-aiplatform>=1.72.0
pydantic>=2.9.0
pydantic-settings>=2.6.0
asyncpg>=0.30.0
alembic>=1.14.0
pymupdf>=1.24.0
pandas>=2.2.0
openpyxl>=3.1.0
cachetools>=5.5.0
python-dotenv>=1.0.0
slowapi>=0.1.9
```

---

## 9. Ordem de Implementação (com TDD) — REVISADA V3

### Mudanças em relação à V2

| Aspecto | V2 | V3 |
|---------|----|----|
| **Settings** | Etapa 11 | **Etapa 1** — dependência transversal |
| **Migrations/Schema** | Etapa 4 | **Etapa 2** — antes de qualquer código |
| **Bootstrap (pyproject, ruff, mypy)** | Não existia | **Etapa 0** — fundação do projeto |
| **Input Ports (inbound)** | Ausente | **Etapa 4** — junto com outbound |
| **PromptBuilder** | Mencionado mas sem lugar | **Etapa 8** — serviço de domínio explícito |
| **Health check evoluído** | Ausente | **Etapa 10** — com status do pool e stats |
| **Validação de migração** | Ausente | **Etapa 6** — checksums e contagem |
| **Idempotência do crawler** | Ausente | **Etapa 11** — upsert com controle |
| **Analytics temporal** | Sem índice | **Etapa 2** — índice `DATE(created_at)` |
| **Total de etapas** | 19 | **16** — consolidação sem perda de escopo |

### Etapas

| Etapa | O que | Testes primeiro | Arquivos |
|-------|-------|----------------|----------|
| **0** | Bootstrap do projeto | — | `pyproject.toml`, `requirements.txt`, `requirements-dev.txt`, `docker-compose.yml`, `.env.example`, config ruff/mypy, `__init__.py` vazios |
| **1** | Settings + conexão com banco | `test_settings.py`, `test_pg_connection.py` | `app/config/settings.py`, `app/adapters/outbound/postgres/connection.py`, `tests/conftest.py` |
| **2** | PostgreSQL schema + Alembic migrations | `test_migrations.py` | `alembic.ini`, `migrations/env.py`, `migrations/versions/001_initial_schema.py` |
| **3** | Domain entities + value objects | `test_entities.py` | `app/domain/entities/`, `app/domain/value_objects/` |
| **4** | Ports (inbound + outbound ABCs) + fakes | — (interfaces não testadas) | `app/domain/ports/inbound/`, `app/domain/ports/outbound/`, fakes em `tests/conftest.py` |
| **5** | PostgreSQL adapters | `test_postgres_*_repo.py` (RED → GREEN) | `postgres/search_repo.py`, `document_repo.py`, `session_repo.py`, `analytics_repo.py` |
| **6** | Migração de dados SQLite → PostgreSQL | `test_migration_data.py` (contagem, checksums) | `scripts/seed_db.py` |
| **7** | Document processor (PDF, CSV, XLSX) | `test_document_processor.py` | `app/adapters/outbound/document_processor/` |
| **8** | Domain services (Search + PromptBuilder + Chat + Document) | `test_search_service.py`, `test_prompt_builder.py`, `test_chat_service.py`, `test_document_service.py` | `app/domain/services/` |
| **9** | Adapters de infraestrutura (Vertex AI + HTTP Fetcher) | `test_vertex_gateway.py`, `test_content_fetcher.py` | `app/adapters/outbound/vertex_ai/`, `app/adapters/outbound/http/` |
| **10** | FastAPI app + DI + routers + health evoluído | `test_chat_api.py`, `test_document_api.py`, `test_session_api.py` | `app/adapters/inbound/fastapi/`, `app/main.py` |
| **11** | Crawler refatorado (com upsert idempotente) | `test_crawler.py` | `app/adapters/inbound/cli/crawler.py`, `scripts/crawler.py` |
| **12** | Frontend: design system + layout 3 painéis | — (visual) | `static/css/`, `static/index.html` |
| **13** | Frontend: chat evoluído (citações, tabelas, feedback) | — (visual) | `static/js/chat.js`, `static/js/utils.js`, `static/js/api.js` |
| **14** | Frontend: mapa + documentos + sessões | — (visual) | `static/js/map.js`, `static/js/documents.js`, `static/js/sessions.js` |
| **15** | Analytics + dashboard admin | `test_analytics.py` | `analytics_repo.py`, `analytics_router.py` |
| **16** | Containerização + deploy OpenShift | — | `Containerfile`, manifests, CI pipeline |

### Critérios de saída por etapa

- **Etapa 0:** `ruff check .` e `mypy app/` passam. `docker compose up db` conecta.
- **Etapa 1:** Settings carrega de `.env`, pool conecta e fecha sem erro.
- **Etapa 2:** `alembic upgrade head` cria todas as tabelas. `alembic downgrade base` remove. Trigger de search_vector funciona.
- **Etapa 3:** Todos os testes de entities passam. Value objects são frozen.
- **Etapa 4:** ABCs importáveis. Fakes disponíveis em conftest.
- **Etapa 5:** Testes de integração passam com testcontainers. FTS com stemming PT funciona.
- **Etapa 6:** `seed_db.py` transfere 100% dos registros com log de verificação.
- **Etapa 7:** PDFs e CSVs de teste processados. Chunks respeitam limites de tokens.
- **Etapa 8:** 40+ testes unitários passam com fakes (zero I/O).
- **Etapa 9:** Mocks do SDK/httpx cobrem cenários de sucesso e erro.
- **Etapa 10:** Endpoints respondem com dados reais. Health check reporta pool + stats.
- **Etapa 11:** Crawler re-executa sem duplicar dados (upsert).
- **Etapa 12-14:** UI funcional em desktop e mobile.
- **Etapa 15:** Dashboard admin com métricas diárias.
- **Etapa 16:** Container roda em OpenShift. CI verde.

---

## 10. Verificação End-to-End

```bash
# 1. Subir ambiente
docker compose up -d db
alembic upgrade head

# 2. Rodar testes
pytest tests/unit/ -v --cov=app/domain --cov-fail-under=80
pytest tests/integration/ -v

# 3. Migrar dados
python scripts/seed_db.py --from-sqlite app/data/knowledge.db

# 4. Processar documentos
python scripts/crawler.py --process-documents

# 5. Validar busca
curl localhost:8080/api/health | jq .
# Deve mostrar:
# {
#   "status": "healthy",
#   "database": "connected",
#   "pool": {"size": 10, "free": 8},
#   "knowledge": {"pages": 890, "documents": 143, "chunks": >0, "tables": >0},
#   "version": "2.0.0",
#   "uptime_seconds": ...
# }

# 6. Testar RAG
curl -X POST localhost:8080/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message": "qual o valor total de despesas com pessoal em 2024?"}'
# Resposta deve conter: text com citação [Fonte: ...], sources[], tables[]

# 7. Testar mapa
curl localhost:8080/api/transparency-map | jq '.[] | .category'
# Deve listar todas as 36 categorias com contagens

# 8. Testar sessão
SESSION=$(curl -s -X POST localhost:8080/api/sessions | jq -r .id)
curl -X POST localhost:8080/api/chat \
  -d "{\"message\": \"licitações em andamento\", \"session_id\": \"$SESSION\"}"
curl localhost:8080/api/sessions/$SESSION/messages | jq length
# Deve retornar 2 (user + assistant)

# 9. Type check + lint
mypy app/ --strict
ruff check app/ tests/

# 10. Coverage report
pytest --cov=app --cov-report=html
open htmlcov/index.html
```
