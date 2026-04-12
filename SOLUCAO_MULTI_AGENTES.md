# Solução Multi-Agentes — Cristal 2.0

## 1. Visão Geral

### Problema

O `ChatService` atual opera com um único LLM call que recebe todo o contexto (páginas, chunks, tabelas) e deve simultaneamente interpretar dados, calcular métricas, formatar tabelas e redigir texto. Isso gera quatro defeitos recorrentes:

1. **Aritmética incorreta** — o LLM erra contagens e somas (diz "37 contratos" quando a tabela tem 51 linhas).
2. **Dupla linha de TOTAL** — o LLM adiciona TOTAL na tabela e o `_fix_metrics_from_tables` tenta corrigir post-hoc, gerando inconsistência.
3. **Prompt monolítico** — ~170 linhas de system prompt com responsabilidades misturadas (formatação JSON + regras de negócio + aritmética).
4. **Três fontes de verdade** — métricas no `text`, na `metrics[]` e na última linha da tabela divergem entre si.

### Solução

Pipeline multi-agente com separação clara de responsabilidades:

| Componente | Tipo | Responsabilidade |
|---|---|---|
| **DataAgent** | LLM + function calling | Analisa a query, seleciona dados relevantes, invoca tools Python para cálculos |
| **WriterAgent** | LLM (text only) | Redige o texto narrativo a partir dos dados já computados |
| **ResponseAssembler** | Python puro | Monta o `ChatMessage` final sem ambiguidade — uma única fonte de verdade |

### Benefícios esperados

- **Aritmética 100% determinística** — count, sum, sort executados em Python, nunca pelo LLM.
- **Eliminação de TOTAL duplicado** — o Assembler controla a única linha de total.
- **Prompts enxutos** — cada agente tem ~30 linhas de system prompt focadas em uma tarefa.
- **Testabilidade** — cada componente testável isoladamente com mocks.
- **Rollout gradual** — feature flag `CRISTAL_USE_MULTI_AGENT` permite A/B entre fluxos.

---

## 2. Diagrama de Arquitetura

```
                         ┌─────────────────────────────────────┐
                         │           Frontend (chat.js)         │
                         │  EventSource(/api/chat/stream)       │
                         └──────────────┬──────────────────────┘
                                        │ SSE
                         ┌──────────────▼──────────────────────┐
                         │      chat_router.py                  │
                         │  POST /api/chat      (JSON)          │
                         │  POST /api/chat/stream (SSE)         │
                         └──────────────┬──────────────────────┘
                                        │
                    ┌───────────────────▼───────────────────────┐
                    │         MultiAgentChatService              │
                    │         (implements ChatUseCase)           │
                    │                                            │
                    │  1. classify intent                        │
                    │  2. search context (pages, chunks, tables) │
                    │  3. emit SSE: "searching"                  │
                    └─────┬────────────────────────┬────────────┘
                          │                        │
               ┌──────────▼──────────┐             │
               │     DataAgent       │             │
               │  (gemini-3-flash)   │             │
               │                     │             │
               │  function calling:  │             │
               │  - filter_rows      │             │
               │  - count_rows       │             │
               │  - sum_column       │             │
               │  - sort_rows        │             │
               │  - extract_value    │             │
               │                     │             │
               │  → emit SSE:        │             │
               │    "analyzing"      │             │
               └──────────┬──────────┘             │
                          │ AnalysisResult         │
               ┌──────────▼──────────┐             │
               │    WriterAgent      │             │
               │ (flash-lite)        │             │
               │                     │             │
               │  → emit SSE:        │
               │    "writing"        │             │
               └──────────┬──────────┘             │
                          │ raw narrative           │
               ┌──────────▼──────────────────────▼─┐
               │     ResponseAssembler              │
               │     (Python puro — zero LLM)       │
               │                                    │
               │  - Monta tables[] com TOTAL único  │
               │  - Calcula metrics[] definitivas   │
               │  - Valida text não repete tabela   │
               │  - Monta ChatMessage final         │
               │                                    │
               │  → emit SSE: "done" + payload      │
               └────────────────────────────────────┘
```

---

## 3. Princípios SOLID Aplicados

### SRP — Single Responsibility Principle

| Classe | Responsabilidade única |
|---|---|
| `DataAgent` | Interpretar a query e invocar tools para extrair/computar dados |
| `WriterAgent` | Redigir texto narrativo a partir de dados pré-computados |
| `ResponseAssembler` | Montar `ChatMessage` final a partir de partes validadas |
| `ToolExecutor` | Executar as tools de function calling (Python puro) |
| `MultiAgentChatService` | Orquestrar o pipeline multi-agente |

O `ChatService` atual viola SRP ao fazer parse JSON, aritmética corretiva, formatação BRL, classificação de intent e orquestração — tudo em uma classe de 320 linhas. A nova arquitetura divide essas responsabilidades em 5 classes.

### OCP — Open/Closed Principle

- Novas tools adicionadas em `ToolRegistry` sem modificar `DataAgent`.
- Novos agentes (ex: `ValidatorAgent`) inseridos no pipeline sem alterar `MultiAgentChatService` — basta implementar a ABC `Agent`.
- O `ToolExecutor` aceita um dicionário de tools; adicionar `average_column` ou `group_by` não requer alteração no executor.

### LSP — Liskov Substitution Principle

- `MultiAgentChatService` implementa `ChatUseCase` — substituível pelo `ChatService` original via feature flag.
- Ambos os gateways (DataAgent usa `gemini-3-flash`, WriterAgent usa `flash-lite`) são instâncias de `LLMGateway` — intercambiáveis.

### ISP — Interface Segregation Principle

- `LLMGateway` já é segregada (`generate` vs `generate_stream`).
- Nova ABC `FunctionCallingGateway` estende apenas o que o DataAgent precisa (`generate_with_tools`), sem poluir `LLMGateway`.
- `ProgressEmitter` é interface separada de `ChatUseCase` — o router injeta, o service usa.

### DIP — Dependency Inversion Principle

- `MultiAgentChatService` depende de ABCs (`LLMGateway`, `SearchRepository`, `ProgressEmitter`), nunca de implementações concretas.
- `DataAgent` recebe `FunctionCallingGateway` e `ToolExecutor` via construtor.
- O wiring concreto ocorre exclusivamente em `app.py` (lifespan).

---

## 4. Novos Componentes

### 4.1 ToolExecutor

```
app/domain/services/tool_executor.py
```

**Responsabilidade:** Executar tools de function calling com aritmética determinística em Python puro.

```python
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    result: Any  # int | Decimal | list[list[str]] | str
    metadata: dict[str, Any]  # ex: {"column": "valor", "count": 51}


class ToolExecutor:
    """Executa tools de function calling — Python puro, zero LLM."""

    def execute(self, tool_name: str, args: dict[str, Any],
                tables: list[dict[str, Any]]) -> ToolResult:
        """Despacha para a função correspondente."""
        ...

    def filter_rows(self, table: dict, column: str,
                    operator: str, value: str) -> ToolResult: ...

    def count_rows(self, table: dict,
                   column: str | None = None,
                   value: str | None = None) -> ToolResult: ...

    def sum_column(self, table: dict, column: str) -> ToolResult: ...

    def sort_rows(self, table: dict, column: str,
                  order: str = "desc") -> ToolResult: ...

    def extract_value(self, table: dict, row_filter: dict,
                      column: str) -> ToolResult: ...
```

**Dependências:** Nenhuma externa. Usa apenas `decimal`, `re`.

**Testabilidade:** Testes unitários puros — fornece tabela em memória, verifica resultado. Zero mocks.

---

### 4.2 DataAgent

```
app/domain/services/data_agent.py
```

**Responsabilidade:** Usar LLM com function calling para analisar a query do usuário e invocar tools para extrair/computar dados.

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.domain.entities.document_table import DocumentTable
from app.domain.ports.outbound.function_calling_gateway import FunctionCallingGateway
from app.domain.services.tool_executor import ToolExecutor, ToolResult
from app.domain.value_objects.search_result import ChunkMatch, PageMatch


@dataclass
class AnalysisResult:
    """Saída estruturada do DataAgent — entrada do WriterAgent."""
    selected_tables: list[DocumentTable]
    computed_metrics: list[ToolResult]
    relevant_chunks: list[str]       # trechos textuais selecionados
    data_summary: str                # resumo gerado pelo DataAgent para o Writer
    tool_calls_log: list[dict[str, Any]]  # auditoria


class DataAgent:
    """Agente de dados — LLM + function calling."""

    def __init__(
        self,
        llm: FunctionCallingGateway,
        tool_executor: ToolExecutor,
        max_tool_rounds: int = 5,
    ) -> None: ...

    async def analyze(
        self,
        query: str,
        pages: list[PageMatch],
        chunks: list[ChunkMatch],
        tables: list[DocumentTable],
    ) -> AnalysisResult: ...
```

**Dependências:** `FunctionCallingGateway` (injetado), `ToolExecutor` (injetado).

**Testabilidade:** Mock do `FunctionCallingGateway` retornando function calls predefinidas. `ToolExecutor` real ou mock — ambos válidos.

---

### 4.3 WriterAgent

```
app/domain/services/writer_agent.py
```

**Responsabilidade:** Redigir texto narrativo (campo `text` do `ChatMessage`) a partir do `AnalysisResult`.

```python
from __future__ import annotations

from app.domain.ports.outbound.llm_gateway import LLMGateway
from app.domain.services.data_agent import AnalysisResult


class WriterAgent:
    """Agente escritor — redige texto a partir de dados pré-computados."""

    def __init__(self, llm: LLMGateway) -> None: ...

    async def write(self, query: str, analysis: AnalysisResult) -> str:
        """Retorna o texto narrativo (Markdown) para o campo 'text'."""
        ...
```

**Dependências:** `LLMGateway` (injetado — instância configurada com flash-lite).

**Testabilidade:** Mock do `LLMGateway`. Verifica que o prompt enviado contém os dados do `AnalysisResult` e não pede cálculos.

---

### 4.4 ResponseAssembler

```
app/domain/services/response_assembler.py
```

**Responsabilidade:** Montar o `ChatMessage` final — fonte única de verdade para métricas, tabelas e texto.

```python
from __future__ import annotations

from app.domain.services.data_agent import AnalysisResult
from app.domain.value_objects.chat_message import ChatMessage, Citation, MetricItem, TableData


class ResponseAssembler:
    """Monta ChatMessage final — Python puro, zero LLM."""

    def assemble(
        self,
        query: str,
        narrative: str,
        analysis: AnalysisResult,
    ) -> ChatMessage:
        """
        1. Converte selected_tables → TableData[] com linha TOTAL única
        2. Converte computed_metrics → MetricItem[]
        3. Extrai Citations dos chunks
        4. Valida que narrative não duplica dados tabulares
        5. Gera suggestions baseadas no contexto
        """
        ...

    def _build_tables(self, analysis: AnalysisResult) -> list[TableData]: ...
    def _build_metrics(self, analysis: AnalysisResult) -> list[MetricItem]: ...
    def _build_citations(self, analysis: AnalysisResult) -> list[Citation]: ...
    def _sanitize_narrative(self, text: str, tables: list[TableData]) -> str: ...
```

**Dependências:** Nenhuma externa. Usa apenas value objects do domínio.

**Testabilidade:** Testes unitários puros — fornece `AnalysisResult` e `narrative`, verifica `ChatMessage` montado.

---

### 4.5 MultiAgentChatService

```
app/domain/services/multi_agent_chat_service.py
```

**Responsabilidade:** Orquestrar o pipeline DataAgent → WriterAgent → Assembler, emitindo eventos de progresso.

```python
from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from app.domain.ports.inbound.chat_use_case import ChatUseCase
from app.domain.ports.outbound.analytics_repository import AnalyticsRepository
from app.domain.ports.outbound.search_repository import SearchRepository
from app.domain.ports.outbound.session_repository import SessionRepository
from app.domain.services.data_agent import DataAgent
from app.domain.services.prompt_builder import PromptBuilder
from app.domain.services.response_assembler import ResponseAssembler
from app.domain.services.table_validator import TableValidatorAgent
from app.domain.services.writer_agent import WriterAgent
from app.domain.value_objects.chat_message import ChatMessage
from app.domain.value_objects.progress_event import ProgressEvent


class MultiAgentChatService(ChatUseCase):
    """Orquestra pipeline multi-agente com progresso via callback."""

    def __init__(
        self,
        search_repo: SearchRepository,
        session_repo: SessionRepository,
        analytics_repo: AnalyticsRepository,
        data_agent: DataAgent,
        writer_agent: WriterAgent,
        assembler: ResponseAssembler,
        prompt_builder: PromptBuilder | None = None,
        table_validator: TableValidatorAgent | None = None,
        top_k: int = 10,
    ) -> None: ...

    async def process_message(
        self,
        message: str,
        session_id: UUID | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> ChatMessage:
        """Fluxo completo — retorna ChatMessage final (compatível com endpoint JSON)."""
        ...

    async def process_message_stream(
        self,
        message: str,
        session_id: UUID | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> AsyncIterator[ProgressEvent]:
        """Fluxo SSE — yield ProgressEvent a cada etapa."""
        ...

    async def get_suggestions(self) -> list[str]: ...
```

**Dependências:** `DataAgent`, `WriterAgent`, `ResponseAssembler` (todos injetados). Repositórios via ABC.

**Testabilidade:** Mock de todos os colaboradores. Verifica sequência de chamadas e eventos emitidos.

---

### 4.6 CircuitBreaker

```
app/domain/services/circuit_breaker.py
```

**Responsabilidade:** Proteger chamadas ao `gemini-3-flash` com fallback para `flash-lite`.

```python
from __future__ import annotations

import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker para LLM gateway com fallback."""

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout: float = 60.0,
    ) -> None: ...

    @property
    def state(self) -> CircuitState: ...

    def record_success(self) -> None: ...
    def record_failure(self) -> None: ...
    def should_use_fallback(self) -> bool: ...
```

**Testabilidade:** Testes unitários com manipulação de tempo (mock de `time.monotonic`).

---

### 4.7 ProgressEvent (value object)

```
app/domain/value_objects/progress_event.py
```

```python
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ProgressEvent:
    event_type: str          # "searching" | "analyzing" | "writing" | "done" | "error"
    message: str             # mensagem legível para o usuário
    data: dict[str, Any] = field(default_factory=dict)  # payload opcional
```

---

## 5. Novos Ports (Interfaces)

### 5.1 FunctionCallingGateway

```
app/domain/ports/outbound/function_calling_gateway.py
```

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FunctionCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class FunctionCallingResponse:
    """Resposta do LLM que pode conter function calls ou texto final."""
    text: str | None                    # None se houver function calls pendentes
    function_calls: list[FunctionCall]  # vazio se for texto final
    finish_reason: str                  # "stop" | "function_call"


class FunctionCallingGateway(ABC):
    """Gateway LLM com suporte a function calling."""

    @abstractmethod
    async def generate_with_tools(
        self,
        system_prompt: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        temperature: float = 0.1,
    ) -> FunctionCallingResponse: ...
```

**Implementação:** `VertexAIFunctionCallingGateway` em `app/adapters/outbound/vertex_ai/function_calling_gateway.py`.

### 5.2 ChatStreamUseCase (extensão opcional)

```
app/domain/ports/inbound/chat_stream_use_case.py
```

```python
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from uuid import UUID

from app.domain.value_objects.progress_event import ProgressEvent


class ChatStreamUseCase(ABC):
    """Port para chat com streaming de progresso via SSE."""

    @abstractmethod
    async def process_message_stream(
        self,
        message: str,
        session_id: UUID | None = None,
        history: list[dict[str, object]] | None = None,
    ) -> AsyncIterator[ProgressEvent]: ...
```

`MultiAgentChatService` implementa **ambos** `ChatUseCase` e `ChatStreamUseCase`.

---

## 6. Alterações em Componentes Existentes

### 6.1 `app/config/settings.py`

Adicionar campos:

```python
# ── Multi-Agent ──────────────────────────────────────────────────────────
use_multi_agent: bool = False                         # feature flag
data_agent_model: str = "gemini-3-flash-preview"      # DataAgent (function calling)
writer_agent_model: str = "gemini-3.1-flash-lite-preview"  # WriterAgent
data_agent_temperature: float = 0.1
writer_agent_temperature: float = 0.3
data_agent_max_tool_rounds: int = 5

# ── Circuit Breaker ──────────────────────────────────────────────────────
circuit_breaker_threshold: int = 3
circuit_breaker_timeout: float = 60.0
data_agent_fallback_model: str = "gemini-3.1-flash-lite-preview"

# ── SSE ──────────────────────────────────────────────────────────────────
sse_enabled: bool = False
sse_keepalive_seconds: int = 15
```

### 6.2 `app/adapters/inbound/fastapi/app.py` (lifespan)

Adicionar wiring condicional baseado em `settings.use_multi_agent`:

```python
if settings.use_multi_agent:
    from app.domain.services.data_agent import DataAgent
    from app.domain.services.writer_agent import WriterAgent
    from app.domain.services.response_assembler import ResponseAssembler
    from app.domain.services.tool_executor import ToolExecutor
    from app.domain.services.circuit_breaker import CircuitBreaker
    from app.adapters.outbound.vertex_ai.function_calling_gateway import (
        VertexAIFunctionCallingGateway,
    )
    from app.domain.services.multi_agent_chat_service import MultiAgentChatService

    # Gateway para DataAgent (modelo mais capaz)
    data_llm = VertexAIFunctionCallingGateway(
        project_id=settings.vertex_project_id,
        location=settings.vertex_location,
        model_name=settings.data_agent_model,
    )

    # Gateway para WriterAgent (modelo leve)
    writer_llm = VertexAIGateway(
        project_id=settings.vertex_project_id,
        location=settings.vertex_location,
        model_name=settings.writer_agent_model,
    )

    circuit_breaker = CircuitBreaker(
        failure_threshold=settings.circuit_breaker_threshold,
        recovery_timeout=settings.circuit_breaker_timeout,
    )

    tool_executor = ToolExecutor()
    data_agent = DataAgent(llm=data_llm, tool_executor=tool_executor)
    writer_agent = WriterAgent(llm=writer_llm)
    assembler = ResponseAssembler()

    app.state.chat_service = MultiAgentChatService(
        search_repo=hybrid_search,
        session_repo=session_repo,
        analytics_repo=analytics_repo,
        data_agent=data_agent,
        writer_agent=writer_agent,
        assembler=assembler,
    )
else:
    app.state.chat_service = ChatService(
        search_repo=hybrid_search,
        session_repo=session_repo,
        analytics_repo=analytics_repo,
        llm=llm,
    )
```

### 6.3 `app/adapters/inbound/fastapi/chat_router.py`

Adicionar endpoint SSE:

```python
from fastapi.responses import StreamingResponse

@router.post("/chat/stream")
async def post_chat_stream(
    body: ChatRequest,
    request: Request,
) -> StreamingResponse:
    """Chat com SSE — progresso em tempo real."""
    chat_service = request.app.state.chat_service
    settings = request.app.state.settings

    if not settings.sse_enabled or not hasattr(chat_service, "process_message_stream"):
        # Fallback: executa síncrono e retorna como SSE com evento único
        ...

    async def event_generator():
        async for event in chat_service.process_message_stream(
            message=body.message,
            session_id=body.session_id,
            history=body.history,
        ):
            yield f"event: {event.event_type}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
```

O endpoint `POST /api/chat` existente permanece inalterado — compatibilidade total.

### 6.4 `app/adapters/outbound/vertex_ai/gateway.py`

Sem alteração na classe existente. Nova classe `VertexAIFunctionCallingGateway` em arquivo separado.

### 6.5 `static/js/chat.js`

Modificar `sendMessage` para usar SSE quando disponível:

```javascript
async function sendMessage(text) {
    state.isLoading = true;
    sendButton.disabled = true;
    inputField.value = '';

    if (!state.sessionId && window.Sessions) {
        state.sessionId = await window.Sessions.ensureSession();
    }

    appendUserMessage(text);

    if (window._useSSE) {
        sendMessageSSE(text);
    } else {
        sendMessageJSON(text);
    }
}

function sendMessageSSE(text) {
    var progressId = appendProgressIndicator();  // novo componente visual

    var body = JSON.stringify({
        message: text,
        history: state.history,
        session_id: state.sessionId,
    });

    // Usa fetch + ReadableStream (POST não suportado por EventSource nativo)
    fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
    }).then(function (response) {
        var reader = response.body.getReader();
        var decoder = new TextDecoder();
        // ... parse SSE events, atualiza progressIndicator, monta resposta final
    });
}
```

A flag `window._useSSE` é definida no `index.html` a partir de um `<meta>` tag ou chamada ao `/api/health`.

---

## 7. Definição das Tools de Function Calling

Schema no formato Vertex AI (`functionDeclarations`):

```python
TOOL_DECLARATIONS = [
    {
        "name": "filter_rows",
        "description": (
            "Filtra linhas de uma tabela onde o valor de uma coluna "
            "atende a uma condição. Retorna a tabela filtrada."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "column": {
                    "type": "string",
                    "description": "Nome exato do cabeçalho da coluna.",
                },
                "operator": {
                    "type": "string",
                    "enum": ["eq", "neq", "gt", "gte", "lt", "lte", "contains", "startswith"],
                    "description": "Operador de comparação.",
                },
                "value": {
                    "type": "string",
                    "description": "Valor para comparação (sempre string — conversão interna).",
                },
            },
            "required": ["table_index", "column", "operator", "value"],
        },
    },
    {
        "name": "count_rows",
        "description": (
            "Conta o número de linhas de uma tabela. "
            "Opcionalmente filtra por coluna/valor antes de contar."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "column": {
                    "type": "string",
                    "description": "Coluna para filtrar antes de contar (opcional).",
                },
                "value": {
                    "type": "string",
                    "description": "Valor para filtrar na coluna (opcional).",
                },
            },
            "required": ["table_index"],
        },
    },
    {
        "name": "sum_column",
        "description": (
            "Soma todos os valores numéricos de uma coluna. "
            "Aceita formatos BRL (R$ 1.234,56) e decimais. "
            "Retorna o total exato como Decimal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "column": {
                    "type": "string",
                    "description": "Nome exato do cabeçalho da coluna numérica.",
                },
            },
            "required": ["table_index", "column"],
        },
    },
    {
        "name": "sort_rows",
        "description": (
            "Ordena as linhas de uma tabela por uma coluna. "
            "Valores numéricos e monetários são ordenados numericamente."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "column": {
                    "type": "string",
                    "description": "Nome exato do cabeçalho da coluna.",
                },
                "order": {
                    "type": "string",
                    "enum": ["asc", "desc"],
                    "description": "Direção da ordenação. Default: desc.",
                },
            },
            "required": ["table_index", "column"],
        },
    },
    {
        "name": "extract_value",
        "description": (
            "Extrai o valor de uma célula específica. "
            "Localiza a linha pelo filtro e retorna o valor da coluna alvo."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_index": {
                    "type": "integer",
                    "description": "Índice da tabela no contexto (0-based).",
                },
                "filter_column": {
                    "type": "string",
                    "description": "Coluna usada para localizar a linha.",
                },
                "filter_value": {
                    "type": "string",
                    "description": "Valor para localizar a linha (match parcial case-insensitive).",
                },
                "target_column": {
                    "type": "string",
                    "description": "Coluna da qual extrair o valor.",
                },
            },
            "required": ["table_index", "filter_column", "filter_value", "target_column"],
        },
    },
]
```

Estas declarações ficam em `app/domain/services/tool_declarations.py` como constante reutilizável.

---

## 8. SSE — Protocolo de Eventos

### Event Types

| event | Quando emitido | Payload (`data`) |
|---|---|---|
| `searching` | Início da busca no banco | `{"message": "Buscando informações..."}` |
| `analyzing` | DataAgent iniciou análise | `{"message": "Analisando dados...", "tables_found": 3}` |
| `tool_call` | DataAgent invocou uma tool | `{"tool": "sum_column", "args": {"column": "Valor"}, "result": "R$ 1.234,56"}` |
| `writing` | WriterAgent iniciou redação | `{"message": "Preparando resposta..."}` |
| `done` | Pipeline concluído | `ChatResponse` completo (mesmo schema do POST /api/chat) |
| `error` | Erro em qualquer etapa | `{"message": "Erro ao processar", "code": "DATA_AGENT_FAILURE"}` |
| `keepalive` | A cada N segundos | `{}` (evita timeout de proxy) |

### Formato SSE

```
event: searching
data: {"message": "Buscando informações relevantes..."}

event: analyzing
data: {"message": "Analisando 3 tabelas encontradas...", "tables_found": 3}

event: tool_call
data: {"tool": "count_rows", "args": {"table_index": 0}, "result": 51}

event: tool_call
data: {"tool": "sum_column", "args": {"table_index": 0, "column": "Valor"}, "result": "R$ 2.345.678,90"}

event: writing
data: {"message": "Preparando resposta..."}

event: done
data: {"text": "Foram encontrados **51 contratos**...", "sources": [...], "tables": [...], "metrics": [...], "suggestions": [...]}
```

### Frontend: Mapeamento visual

| event | Indicador visual |
|---|---|
| `searching` | Ícone de lupa + "Buscando informações..." |
| `analyzing` | Ícone de tabela + "Analisando N tabelas..." |
| `tool_call` | Subtexto: "Calculando soma de Valor..." |
| `writing` | Ícone de escrita + "Preparando resposta..." |
| `done` | Remove indicador, renderiza resposta completa |
| `error` | Mensagem de erro vermelha |

---

## 9. Estratégia de Testes

### 9.1 Testes Unitários (mocks)

| Componente | Arquivo de teste | O que testar |
|---|---|---|
| `ToolExecutor` | `tests/unit/test_tool_executor.py` | Cada tool com tabelas variadas: vazias, com BRL, com NaN, com acentos |
| `DataAgent` | `tests/unit/test_data_agent.py` | Mock de `FunctionCallingGateway`: verifica loop de tool calls, max rounds, fallback |
| `WriterAgent` | `tests/unit/test_writer_agent.py` | Mock de `LLMGateway`: verifica prompt contém dados pré-computados |
| `ResponseAssembler` | `tests/unit/test_response_assembler.py` | Monta `ChatMessage` a partir de `AnalysisResult` + narrative: verifica TOTAL único, metrics corretas |
| `MultiAgentChatService` | `tests/unit/test_multi_agent_chat_service.py` | Mock de todos os colaboradores: verifica sequência de chamadas e eventos de progresso |
| `CircuitBreaker` | `tests/unit/test_circuit_breaker.py` | Transições de estado: closed→open→half_open→closed |

**Prioridade:** `ToolExecutor` e `ResponseAssembler` são os mais críticos — testam a aritmética determinística.

Exemplos de cenários para `ToolExecutor`:

```python
def test_count_rows_excludes_total_row():
    table = {"headers": ["Nome", "Valor"], "rows": [
        ["A", "100"], ["B", "200"], ["TOTAL", "300"]
    ]}
    result = executor.count_rows(table)
    assert result.result == 2  # Exclui linha TOTAL

def test_sum_column_brl_format():
    table = {"headers": ["Item", "Valor"], "rows": [
        ["A", "R$ 1.234,56"], ["B", "R$ 2.345,67"]
    ]}
    result = executor.sum_column(table, column="Valor")
    assert result.result == Decimal("3580.23")
```

### 9.2 Testes de Integração (Gemini real em staging)

| Cenário | O que validar |
|---|---|
| DataAgent + Gemini Flash | LLM retorna function calls válidas para query "quantos contratos em 2025?" |
| WriterAgent + Gemini Flash Lite | LLM redige texto coerente a partir de `AnalysisResult` |
| Pipeline completo | Query → busca real → DataAgent → WriterAgent → Assembler → ChatMessage correto |

Estes testes são marcados com `@pytest.mark.integration` e rodam apenas em CI/staging com credenciais Vertex AI.

---

## 10. Plano de Implementação em Fases

### Fase 1 — Fundação: ToolExecutor + testes

**Objetivo:** Implementar e validar a camada de aritmética determinística.

**Arquivos a criar:**
- `app/domain/services/tool_executor.py`
- `app/domain/services/tool_declarations.py`
- `app/domain/value_objects/progress_event.py`
- `tests/unit/test_tool_executor.py`

**Critério de conclusão:**
- 100% dos testes unitários do `ToolExecutor` passando
- Cobertura: `filter_rows`, `count_rows`, `sum_column`, `sort_rows`, `extract_value`
- Cenários edge case: tabela vazia, coluna inexistente, valores não-numéricos, formato BRL

**Dependências:** Nenhuma.

---

### Fase 2 — FunctionCallingGateway + DataAgent

**Objetivo:** Implementar o DataAgent com function calling via Vertex AI.

**Arquivos a criar:**
- `app/domain/ports/outbound/function_calling_gateway.py`
- `app/adapters/outbound/vertex_ai/function_calling_gateway.py`
- `app/domain/services/data_agent.py`
- `tests/unit/test_data_agent.py`

**Critério de conclusão:**
- DataAgent resolve query "quantos contratos?" invocando `count_rows` (com mock)
- Loop de tool calls funciona até `max_tool_rounds`
- Teste de integração opcional com Gemini Flash real

**Dependências:** Fase 1 (ToolExecutor).

---

### Fase 3 — WriterAgent + ResponseAssembler

**Objetivo:** Completar os componentes de saída do pipeline.

**Arquivos a criar:**
- `app/domain/services/writer_agent.py`
- `app/domain/services/response_assembler.py`
- `tests/unit/test_writer_agent.py`
- `tests/unit/test_response_assembler.py`

**Critério de conclusão:**
- `ResponseAssembler` gera `ChatMessage` com TOTAL único e métricas corretas
- `WriterAgent` envia prompt com dados pré-computados (verificado via mock)
- `_sanitize_narrative` detecta e remove dados tabulares do texto

**Dependências:** Fase 2 (DataAgent, para `AnalysisResult`).

---

### Fase 4 — MultiAgentChatService + Circuit Breaker

**Objetivo:** Orquestrador principal + resiliência.

**Arquivos a criar:**
- `app/domain/services/multi_agent_chat_service.py`
- `app/domain/services/circuit_breaker.py`
- `app/domain/ports/inbound/chat_stream_use_case.py`
- `tests/unit/test_multi_agent_chat_service.py`
- `tests/unit/test_circuit_breaker.py`

**Arquivos a modificar:**
- `app/config/settings.py` — adicionar campos multi-agent
- `app/adapters/inbound/fastapi/app.py` — wiring condicional

**Critério de conclusão:**
- `MultiAgentChatService` implementa `ChatUseCase` (compatível com router existente)
- Feature flag `use_multi_agent=false` → usa `ChatService` original
- Feature flag `use_multi_agent=true` → usa pipeline multi-agente
- Circuit breaker faz fallback após N falhas consecutivas

**Dependências:** Fases 1–3.

---

### Fase 5 — SSE Backend + Frontend

**Objetivo:** Streaming de progresso via Server-Sent Events.

**Arquivos a criar:**
- Nenhum novo (apenas modificações)

**Arquivos a modificar:**
- `app/adapters/inbound/fastapi/chat_router.py` — endpoint `/api/chat/stream`
- `app/adapters/inbound/fastapi/schemas.py` — schema `SSEEventOut` (opcional)
- `static/js/chat.js` — `sendMessageSSE()`, progress indicator UI
- `static/css/style.css` — estilos do progress indicator

**Critério de conclusão:**
- Endpoint SSE emite eventos em tempo real (verificável com `curl`)
- Frontend renderiza indicador de progresso por etapa
- Fallback gracioso: se SSE falhar, usa endpoint JSON padrão
- Keepalive evita timeout de proxy (OpenShift)

**Dependências:** Fase 4 (MultiAgentChatService com `process_message_stream`).

---

## 11. Riscos e Mitigações

| Risco | Severidade | Mitigação |
|---|---|---|
| Gemini Flash não suporta function calling adequadamente | Alta | Circuit breaker com fallback para flash-lite. Testes de integração em staging antes de cada release. |
| Latência do pipeline multi-agente (2 LLM calls) > 10s | Média | SSE mostra progresso real — UX não bloqueia. WriterAgent usa flash-lite (rápido). DataAgent paraleliza tool execution. |
| LLM gera function calls com parâmetros inválidos (coluna inexistente) | Média | `ToolExecutor.execute()` retorna erro estruturado; DataAgent recebe feedback e retenta com parâmetros corrigidos (self-correction loop). |
| Feature flag divide fluxo — dois caminhos para manter | Baixa | Após validação em produção (2-4 semanas), remover `ChatService` original e o flag. Testes cobrem ambos os caminhos. |
| SSE bloqueado por proxy reverso do OpenShift | Média | Header `X-Accel-Buffering: no`. Keepalive a cada 15s. Teste em ambiente OpenShift antes do deploy. Fallback para JSON. |
| Custo de API dobra (2 LLM calls por query) | Baixa | WriterAgent usa flash-lite (custo mínimo). DataAgent Flash é invocado apenas para queries com tabelas (classificação por intent filtra). |
| `ToolExecutor` não cobre formato de dados inesperado | Média | Testes unitários extensivos com dados reais do portal TRE-PI. Logging de tool calls para detectar falhas em produção. |

---

## 12. Decisões de Design Registradas

### ADR-001: Aritmética via Function Calling (não post-processing)

**Contexto:** O `ChatService` atual usa `_fix_metrics_from_tables` para corrigir métricas do LLM após o fato. Isso é frágil — depende de heurísticas de nome de label.

**Decisão:** Toda aritmética (contagem, soma, ordenação) é executada pelo `ToolExecutor` em Python puro, invocado via function calling pelo DataAgent. O LLM nunca calcula — apenas decide *qual* operação executar.

**Consequência:** `_fix_metrics_from_tables` e `_sum_value_column` podem ser removidos do `ChatService`. A aritmética é testável e determinística.

---

### ADR-002: Dois modelos LLM (Flash + Flash Lite)

**Contexto:** Function calling exige modelo mais capaz. Redação de texto não exige.

**Decisão:** DataAgent usa `gemini-3-flash-preview` (melhor function calling). WriterAgent usa `gemini-3.1-flash-lite-preview` (mais rápido, mais barato).

**Consequência:** Custo marginal baixo (flash-lite é ~10x mais barato). Circuit breaker cobre degradação do Flash.

---

### ADR-003: ResponseAssembler como fonte única de verdade

**Contexto:** Hoje, métricas aparecem em 3 lugares (text, metrics[], última linha da tabela) e divergem.

**Decisão:** O `ResponseAssembler` é o único componente que monta `ChatMessage`. Ele:
1. Calcula métricas a partir dos `ToolResult` do DataAgent (única fonte).
2. Adiciona linha TOTAL à tabela (única vez).
3. Verifica que o `text` do WriterAgent não repete dados da tabela.

**Consequência:** Elimina a tríplice inconsistência. Se um valor está errado, há um único lugar para debugar.

---

### ADR-004: SSE via POST (não EventSource nativo)

**Contexto:** `EventSource` nativo do browser suporta apenas GET. O chat envia body com message, history e session_id.

**Decisão:** Usar `fetch()` com `ReadableStream` para consumir SSE de um endpoint POST. O backend usa `StreamingResponse` do FastAPI com `text/event-stream`.

**Consequência:** Funciona com qualquer body size. Não requer workarounds como query params para history. Compatível com todos os browsers modernos.

---

## Apêndice A — Estrutura Final de Arquivos (novos)

```
app/
├── domain/
│   ├── ports/
│   │   ├── inbound/
│   │   │   ├── chat_use_case.py              # (existente, inalterado)
│   │   │   └── chat_stream_use_case.py       # NOVO
│   │   └── outbound/
│   │       ├── llm_gateway.py                # (existente, inalterado)
│   │       └── function_calling_gateway.py   # NOVO
│   ├── services/
│   │   ├── chat_service.py                   # (existente — mantido para fallback)
│   │   ├── multi_agent_chat_service.py       # NOVO — orquestrador
│   │   ├── data_agent.py                     # NOVO
│   │   ├── writer_agent.py                   # NOVO
│   │   ├── response_assembler.py             # NOVO
│   │   ├── tool_executor.py                  # NOVO
│   │   ├── tool_declarations.py              # NOVO
│   │   └── circuit_breaker.py                # NOVO
│   └── value_objects/
│       ├── chat_message.py                   # (existente, inalterado)
│       └── progress_event.py                 # NOVO
├── adapters/
│   └── outbound/vertex_ai/
│       ├── gateway.py                        # (existente, inalterado)
│       └── function_calling_gateway.py       # NOVO
└── config/
    └── settings.py                           # MODIFICADO — novos campos

tests/
├── unit/
│   ├── test_tool_executor.py                 # NOVO
│   ├── test_data_agent.py                    # NOVO
│   ├── test_writer_agent.py                  # NOVO
│   ├── test_response_assembler.py            # NOVO
│   ├── test_multi_agent_chat_service.py      # NOVO
│   └── test_circuit_breaker.py               # NOVO
└── e2e/
    └── test_multi_agent_e2e.py               # NOVO
```
