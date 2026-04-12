# Plano de Implementação — Pipeline de Ingestão de Documentos e Bootstrap

## Contexto

O Cristal 2.0 possui 890 páginas indexadas e 122 documentos catalogados (91 PDFs + 31 CSVs) no PostgreSQL. Porém, apenas os **metadados** dos documentos existem (tabela `documents`). As tabelas `document_contents`, `document_chunks` e `document_tables` estão **vazias** — o chatbot não consegue responder perguntas sobre o conteúdo real dos documentos.

O código do `DocumentProcessor`, `PdfProcessor`, `CsvProcessor` e `TextChunker` já existe e está testado unitariamente, mas **nunca é chamado**. Falta o orquestrador que conecta: download → processamento → persistência.

### Estado atual

| Componente | Status |
|---|---|
| `documents` (metadados/links) | 122 registros |
| `document_contents` (texto extraído) | **0 registros** |
| `document_chunks` (segmentos para RAG) | **0 registros** |
| `document_tables` (tabelas extraídas) | **0 registros** |
| `DocumentProcessor` (código) | Existe, nunca chamado |
| `DocumentRepository.save_content()` | Existe, nunca chamado |
| `SearchRepository.search_chunks()` | Existe, retorna vazio |
| `SearchRepository.search_tables()` | Existe, retorna vazio |

### Componentes existentes que serão reutilizados

```
app/adapters/outbound/document_processor/
├── document_processor.py   → DocumentProcessor.process(url, content, doc_type) → ProcessedDocument
├── pdf_processor.py        → PdfProcessor.process(content, document_url) → ProcessedDocument
├── csv_processor.py        → CsvProcessor.process(content, document_url, doc_type) → ProcessedDocument
└── chunker.py              → TextChunker.chunk(text, document_url, ...) → list[DocumentChunk]

app/adapters/outbound/postgres/
├── document_repo.py        → PostgresDocumentRepository.save_content(document_url, content)
└── search_repo.py          → search_chunks(), search_tables()

app/domain/ports/outbound/
└── document_repository.py  → ProcessedDocument (dataclass), DocumentRepository (ABC)
```

---

## Arquitetura da Pipeline

```
┌──────────────────────────────────────────────────────────────┐
│                    CLI: document_ingester.py                  │
│                                                              │
│  python -m app.adapters.inbound.cli.document_ingester        │
│    --run       Processa todos os documentos pendentes        │
│    --reprocess Reprocessa documentos com erro                │
│    --status    Mostra estatísticas de processamento          │
│    --url URL   Processa um único documento                   │
└──────────────────┬───────────────────────────────────────────┘
                   │
                   ▼
┌──────────────────────────────────────────────────────────────┐
│              DocumentIngestionService (domínio)               │
│                                                              │
│  Porta de entrada: DocumentIngestionUseCase (ABC)            │
│                                                              │
│  Responsabilidades:                                          │
│    1. Consultar documentos pendentes (documents table)       │
│    2. Orquestrar download → processamento → persistência     │
│    3. Controlar concorrência e retry                         │
│    4. Atualizar status (pending → processing → done/error)   │
└──────┬──────────┬──────────┬──────────┬──────────────────────┘
       │          │          │          │
       ▼          ▼          ▼          ▼
  HttpClient  DocProcessor  DocRepo   AnalyticsRepo
  (download)  (extração)    (save)    (log)
```

---

## Etapas de Implementação

### Etapa 0 — Bootstrap e automação de deploy do zero
**Objetivo:** Permitir que o sistema suba do zero (banco vazio) sem intervenção manual, executando automaticamente: migrations → crawler → ingestão de documentos.

**Problema atual:**
- O Containerfile executa `uvicorn` diretamente — se o banco não tem schema, o app crasha na primeira query
- O `docker-compose.yml` não orquestra migrations nem população de dados
- O lifespan do FastAPI (`app.py`) não valida se as tabelas existem
- Variáveis de ambiente usam prefixo `CRISTAL_` no settings.py mas `DATABASE_URL` (sem prefixo) no docker-compose
- Não existem manifests para OpenShift (DeploymentConfig, InitContainer, ConfigMap, Secret)

**Arquivos:**

1. `scripts/docker-entrypoint.sh` — Entrypoint inteligente que substitui o CMD do Containerfile
   ```bash
   #!/bin/bash
   set -e

   echo "=== Cristal Bootstrap ==="

   # 1. Aguardar PostgreSQL
   echo "[1/4] Aguardando PostgreSQL..."
   until python -c "
   import asyncio, asyncpg, os
   async def check():
       dsn = os.environ.get('CRISTAL_DATABASE_URL', '')
       dsn = dsn.replace('postgresql+asyncpg://', 'postgresql://')
       await asyncpg.connect(dsn, timeout=5)
   asyncio.run(check())
   " 2>/dev/null; do
       echo "  PostgreSQL indisponível, tentando novamente em 2s..."
       sleep 2
   done
   echo "  PostgreSQL disponível."

   # 2. Aplicar migrations
   echo "[2/4] Aplicando migrations..."
   alembic upgrade head
   echo "  Migrations aplicadas."

   # 3. Verificar se banco tem dados (crawler)
   PAGES_COUNT=$(python -c "
   import asyncio, asyncpg, os
   async def count():
       dsn = os.environ.get('CRISTAL_DATABASE_URL', '')
       dsn = dsn.replace('postgresql+asyncpg://', 'postgresql://')
       conn = await asyncpg.connect(dsn)
       n = await conn.fetchval('SELECT COUNT(*) FROM pages')
       await conn.close()
       print(n)
   asyncio.run(count())
   ")

   if [ "$PAGES_COUNT" -eq 0 ]; then
       echo "[3/4] Banco vazio — executando crawler..."
       python -m app.adapters.inbound.cli.crawler --full
   else
       echo "[3/4] Banco já possui $PAGES_COUNT páginas — crawler ignorado."
   fi

   # 4. Ingestão de documentos (se pipeline implementado)
   if python -c "import app.adapters.inbound.cli.document_ingester" 2>/dev/null; then
       echo "[4/4] Verificando documentos pendentes..."
       python -m app.adapters.inbound.cli.document_ingester --run
   else
       echo "[4/4] Pipeline de ingestão não disponível — ignorado."
   fi

   echo "=== Bootstrap concluído ==="

   # Iniciar app
   exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
   ```

2. `Containerfile` — Atualizar para usar o entrypoint
   ```dockerfile
   FROM python:3.12-slim

   WORKDIR /app

   # Deps do sistema para PyMuPDF e processamento de docs
   RUN apt-get update && apt-get install -y --no-install-recommends \
       libmupdf-dev && rm -rf /var/lib/apt/lists/*

   COPY requirements.txt .
   RUN pip install --no-cache-dir -r requirements.txt

   COPY alembic.ini .
   COPY migrations/ ./migrations/
   COPY app/ ./app/
   COPY scripts/ ./scripts/
   COPY static/ ./static/

   ENV GOOGLE_APPLICATION_CREDENTIALS=/secrets/gcp-sa-key.json
   ENV CRISTAL_VERTEX_PROJECT_ID=tre-pi-project
   ENV CRISTAL_VERTEX_LOCATION=us-central1
   ENV CRISTAL_VERTEX_MODEL=gemini-2.5-flash-lite
   ENV PORT=8080

   EXPOSE 8080

   RUN useradd -u 1001 -r -g 0 -s /sbin/nologin appuser && \
       chmod +x scripts/docker-entrypoint.sh && \
       chown -R 1001:0 /app && \
       chmod -R g=u /app
   USER 1001

   ENTRYPOINT ["scripts/docker-entrypoint.sh"]
   ```

3. `docker-compose.yml` — Corrigir variáveis e adicionar serviço de bootstrap
   ```yaml
   services:
     db:
       image: postgres:16-alpine
       environment:
         POSTGRES_DB: cristal
         POSTGRES_USER: cristal
         POSTGRES_PASSWORD: cristal_dev
       ports:
         - "5432:5432"
       volumes:
         - postgres_data:/var/lib/postgresql/data
       healthcheck:
         test: ["CMD-SHELL", "pg_isready -U cristal -d cristal"]
         interval: 5s
         timeout: 5s
         retries: 5

     app:
       build:
         context: .
         dockerfile: Containerfile
       ports:
         - "8080:8080"
       environment:
         CRISTAL_DATABASE_URL: postgresql+asyncpg://cristal:cristal_dev@db:5432/cristal
         CRISTAL_VERTEX_PROJECT_ID: ${VERTEX_PROJECT_ID}
         CRISTAL_VERTEX_LOCATION: ${VERTEX_LOCATION:-us-central1}
         CRISTAL_VERTEX_MODEL: ${VERTEX_MODEL:-gemini-2.5-flash-lite}
         CRISTAL_GOOGLE_APPLICATION_CREDENTIALS: /app/credentials.json
         CRISTAL_ALLOWED_ORIGINS: "http://localhost:8080"
         CRISTAL_LOG_LEVEL: info
       volumes:
         - ${GOOGLE_APPLICATION_CREDENTIALS:-./credentials.json}:/app/credentials.json:ro
       depends_on:
         db:
           condition: service_healthy

   volumes:
     postgres_data:
   ```

4. `app/adapters/inbound/fastapi/app.py` — Adicionar verificação no lifespan
   ```python
   # No _default_lifespan(), após criar o pool:
   # Verificar se schema existe
   async with pool.acquire() as conn:
       tables = await conn.fetchval(
           "SELECT COUNT(*) FROM information_schema.tables "
           "WHERE table_schema='public' AND table_name='pages'"
       )
       if tables == 0:
           logger.error("Schema não encontrado. Execute: alembic upgrade head")
           raise RuntimeError("Database schema not initialized")
   ```

5. `openshift/` — Manifests para deploy em OpenShift 4.x

   `openshift/deployment.yaml`:
   ```yaml
   apiVersion: apps/v1
   kind: Deployment
   metadata:
     name: cristal
     labels:
       app: cristal
   spec:
     replicas: 1
     selector:
       matchLabels:
         app: cristal
     template:
       metadata:
         labels:
           app: cristal
       spec:
         initContainers:
           - name: db-migrate
             image: cristal:latest
             command: ["alembic", "upgrade", "head"]
             envFrom:
               - configMapRef:
                   name: cristal-config
               - secretRef:
                   name: cristal-secrets
         containers:
           - name: cristal
             image: cristal:latest
             ports:
               - containerPort: 8080
             envFrom:
               - configMapRef:
                   name: cristal-config
               - secretRef:
                   name: cristal-secrets
             volumeMounts:
               - name: gcp-credentials
                 mountPath: /secrets
                 readOnly: true
             readinessProbe:
               httpGet:
                 path: /api/health
                 port: 8080
               initialDelaySeconds: 10
               periodSeconds: 15
             livenessProbe:
               httpGet:
                 path: /api/health
                 port: 8080
               initialDelaySeconds: 30
               periodSeconds: 30
         volumes:
           - name: gcp-credentials
             secret:
               secretName: gcp-sa-key
   ```

   `openshift/configmap.yaml`:
   ```yaml
   apiVersion: v1
   kind: ConfigMap
   metadata:
     name: cristal-config
   data:
     CRISTAL_VERTEX_PROJECT_ID: "tre-pi-project"
     CRISTAL_VERTEX_LOCATION: "us-central1"
     CRISTAL_VERTEX_MODEL: "gemini-2.5-flash-lite"
     CRISTAL_ALLOWED_ORIGINS: "https://transparencia.tre-pi.jus.br"
     CRISTAL_LOG_LEVEL: "info"
     CRISTAL_RATE_LIMIT_PER_MINUTE: "10"
   ```

   `openshift/secret.yaml` (template — valores reais nunca no repo):
   ```yaml
   apiVersion: v1
   kind: Secret
   metadata:
     name: cristal-secrets
   type: Opaque
   stringData:
     CRISTAL_DATABASE_URL: "postgresql+asyncpg://cristal:SENHA@db-host:5432/cristal"
   ```

   `openshift/service.yaml`:
   ```yaml
   apiVersion: v1
   kind: Service
   metadata:
     name: cristal
   spec:
     selector:
       app: cristal
     ports:
       - port: 8080
         targetPort: 8080
   ```

   `openshift/route.yaml`:
   ```yaml
   apiVersion: route.openshift.io/v1
   kind: Route
   metadata:
     name: cristal
   spec:
     to:
       kind: Service
       name: cristal
     port:
       targetPort: 8080
     tls:
       termination: edge
       insecureEdgeTerminationPolicy: Redirect
   ```

   `openshift/cronjob-crawler.yaml` — Execução periódica do crawler
   ```yaml
   apiVersion: batch/v1
   kind: CronJob
   metadata:
     name: cristal-crawler
   spec:
     schedule: "0 3 * * 0"  # Domingos às 3h
     jobTemplate:
       spec:
         template:
           spec:
             containers:
               - name: crawler
                 image: cristal:latest
                 command:
                   - python
                   - -m
                   - app.adapters.inbound.cli.crawler
                   - --update
                 envFrom:
                   - configMapRef:
                       name: cristal-config
                   - secretRef:
                       name: cristal-secrets
             restartPolicy: OnFailure
   ```

   `openshift/cronjob-ingester.yaml` — Ingestão periódica de novos documentos
   ```yaml
   apiVersion: batch/v1
   kind: CronJob
   metadata:
     name: cristal-ingester
   spec:
     schedule: "30 3 * * 0"  # Domingos às 3h30 (após crawler)
     jobTemplate:
       spec:
         template:
           spec:
             containers:
               - name: ingester
                 image: cristal:latest
                 command:
                   - python
                   - -m
                   - app.adapters.inbound.cli.document_ingester
                   - --run
                 envFrom:
                   - configMapRef:
                       name: cristal-config
                   - secretRef:
                       name: cristal-secrets
             restartPolicy: OnFailure
   ```

**Testes:**
- `tests/integration/test_bootstrap.py`
  - Banco vazio → entrypoint cria schema, crawlea, ingere documentos
  - Banco com schema e dados → entrypoint apenas sobe o app
  - Banco com schema sem dados → entrypoint crawlea, ingere
  - Lifespan rejeita app se schema inexistente (sem entrypoint)
- `tests/unit/test_docker_compose_env.py`
  - Variáveis usam prefixo `CRISTAL_` consistente
  - Credenciais montam no path correto

**Critério de aceite:**
- `docker-compose up` em ambiente limpo resulta em sistema funcional com dados
- `oc apply -f openshift/` deploya no OpenShift com migrations automáticas
- CronJobs mantêm dados atualizados semanalmente
- App **não** sobe se schema não existe (fail-fast)

**Fluxo completo de deploy do zero:**

```
docker-compose up
       │
       ▼
   db: PostgreSQL sobe
       │ healthcheck ok
       ▼
   app: docker-entrypoint.sh
       │
       ├─ [1] Aguarda PostgreSQL ✓
       ├─ [2] alembic upgrade head (cria 10 tabelas + views + triggers)
       ├─ [3] Banco vazio? → crawler --full (890 páginas + 122 docs + links)
       ├─ [4] document_ingester --run (processa 122 PDFs/CSVs → chunks + tabelas)
       └─ [5] uvicorn app.main:app (sistema 100% funcional)
```

---

### Etapa 1 — Port de ingestão e status de processamento
**Objetivo:** Definir a interface do serviço e adicionar coluna de controle na tabela `documents`.

**Arquivos:**

1. `migrations/versions/002_document_processing_status.py`
   - Adicionar coluna `processing_status` à tabela `documents` (`pending | processing | done | error`)
   - Adicionar coluna `processing_error` à tabela `documents` (TEXT, nullable)
   - Adicionar coluna `processed_at` à tabela `documents` (TIMESTAMPTZ, nullable)
   - DEFAULT: `'pending'` para registros existentes
   - Índice: `idx_documents_processing_status`

2. `app/domain/ports/inbound/document_ingestion_use_case.py`
   ```python
   class DocumentIngestionUseCase(ABC):
       @abstractmethod
       async def ingest_pending(self, concurrency: int = 3) -> IngestionStats

       @abstractmethod
       async def ingest_single(self, document_url: str) -> bool

       @abstractmethod
       async def reprocess_errors(self) -> IngestionStats

       @abstractmethod
       async def get_status(self) -> IngestionStatus
   ```

3. `app/domain/value_objects/ingestion.py`
   ```python
   @dataclass(frozen=True)
   class IngestionStats:
       total: int
       processed: int
       errors: int
       skipped: int
       duration_seconds: float

   @dataclass(frozen=True)
   class IngestionStatus:
       pending: int
       processing: int
       done: int
       error: int
       total_chunks: int
       total_tables: int
   ```

**Testes:**
- `tests/unit/test_ingestion_value_objects.py` — criação e imutabilidade dos VOs
- `tests/integration/test_migration_002.py` — migração cria colunas e índice

**Critério de aceite:**
- `alembic upgrade head` aplica a migração sem erro
- 122 documentos existentes ganham `processing_status = 'pending'`

---

### Etapa 2 — Gateway de download HTTP
**Objetivo:** Criar adapter para baixar documentos do portal TRE-PI com retry e timeout.

**Arquivos:**

1. `app/domain/ports/outbound/document_download_gateway.py`
   ```python
   class DocumentDownloadGateway(ABC):
       @abstractmethod
       async def download(self, url: str) -> DownloadResult

   @dataclass(frozen=True)
   class DownloadResult:
       content: bytes
       content_type: str
       size_bytes: int
       status_code: int
   ```

2. `app/adapters/outbound/http/document_downloader.py`
   ```python
   class HttpDocumentDownloader(DocumentDownloadGateway):
       def __init__(self, timeout: float = 30.0, max_retries: int = 2)
       async def download(self, url: str) -> DownloadResult
   ```
   - Restrição de domínio: apenas `tre-pi.jus.br`
   - Timeout: 30s por documento
   - Retry: 2 tentativas com backoff exponencial (1s, 2s)
   - Limite de tamanho: 50MB (settings.max_document_size_mb)
   - User-Agent institucional

**Testes:**
- `tests/unit/test_document_downloader.py` — mock httpx, testa retry, timeout, domain check, size limit

**Critério de aceite:**
- Download de PDF e CSV do portal TRE-PI funciona
- URLs fora de `tre-pi.jus.br` são rejeitadas
- Documentos > 50MB são ignorados com log

---

### Etapa 3 — Extensão do DocumentRepository
**Objetivo:** Adicionar métodos para controle de status no repository.

**Arquivos:**

1. `app/domain/ports/outbound/document_repository.py` — adicionar métodos:
   ```python
   @abstractmethod
   async def list_pending(self, limit: int = 50) -> list[Document]

   @abstractmethod
   async def list_errors(self) -> list[Document]

   @abstractmethod
   async def update_status(
       self, document_url: str, status: str, error: str | None = None
   ) -> None
   ```

2. `app/adapters/outbound/postgres/document_repo.py` — implementar:
   ```python
   async def list_pending(self, limit: int = 50) -> list[Document]
   # SELECT ... FROM documents WHERE processing_status = 'pending' LIMIT $1

   async def list_errors(self) -> list[Document]
   # SELECT ... FROM documents WHERE processing_status = 'error'

   async def update_status(self, document_url: str, status: str, error: str | None = None) -> None
   # UPDATE documents SET processing_status=$2, processing_error=$3, processed_at=NOW()
   # WHERE document_url = $1
   ```

**Testes:**
- `tests/integration/test_document_repo_status.py` — list_pending, update_status, list_errors com banco real

**Critério de aceite:**
- `list_pending()` retorna documentos com status 'pending'
- `update_status()` atualiza status e timestamp
- Status inválido lança exceção

---

### Etapa 4 — DocumentIngestionService (domínio)
**Objetivo:** Serviço de domínio que orquestra o pipeline completo.

**Arquivo:** `app/domain/services/document_ingestion_service.py`

```python
class DocumentIngestionService(DocumentIngestionUseCase):
    def __init__(
        self,
        doc_repo: DocumentRepository,
        downloader: DocumentDownloadGateway,
        processor: DocumentProcessGateway,
        concurrency: int = 3,
    ) -> None

    async def ingest_pending(self, concurrency: int = 3) -> IngestionStats:
        """
        Pipeline principal:
        1. doc_repo.list_pending()
        2. Para cada documento (com Semaphore de concorrência):
           a. doc_repo.update_status(url, 'processing')
           b. downloader.download(url)
           c. processor.process(url, content, doc_type)
           d. doc_repo.save_content(url, processed_doc)
           e. doc_repo.update_status(url, 'done')
           f. Em caso de erro: doc_repo.update_status(url, 'error', str(e))
        3. Retorna IngestionStats
        """

    async def ingest_single(self, document_url: str) -> bool:
        """Processa um único documento por URL."""

    async def reprocess_errors(self) -> IngestionStats:
        """Reseta status de 'error' para 'pending' e executa ingest_pending."""

    async def get_status(self) -> IngestionStatus:
        """Retorna contagens por status + totais de chunks/tables."""
```

**Fluxo de um documento:**

```
                ┌─ update_status('processing') ─┐
                │                                │
list_pending() ─┤   download(url)                │
                │        │                       │
                │   process(url, bytes, type)     │
                │        │                       │
                │   save_content(url, result)     │
                │        │                       │
                │   update_status('done')  ───────┘
                │                                │
                └── SE ERRO: update_status('error', msg)
```

**Testes:**
- `tests/unit/test_document_ingestion_service.py`
  - Fluxo feliz: pending → processing → done
  - Erro de download: status muda para 'error' com mensagem
  - Erro de processamento: status muda para 'error'
  - Concorrência: Semaphore limita execuções paralelas
  - `ingest_single`: processa documento específico
  - `reprocess_errors`: reseta e reprocessa
  - `get_status`: retorna contagens corretas

**Critério de aceite:**
- Pipeline processa documento end-to-end (download → chunking → persistência)
- Erros são capturados e registrados sem interromper o lote
- Status é atualizado atomicamente em cada transição

---

### Etapa 5 — CLI Adapter
**Objetivo:** Interface de linha de comando para executar a pipeline.

**Arquivo:** `app/adapters/inbound/cli/document_ingester.py`

```python
class DocumentIngesterCLI:
    def __init__(self, service: DocumentIngestionUseCase) -> None

    async def run(self, concurrency: int = 3) -> None:
        """Executa ingestão com progress bar."""

    async def reprocess(self) -> None:
        """Reprocessa documentos com erro."""

    async def status(self) -> None:
        """Imprime estatísticas formatadas."""

    async def single(self, url: str) -> None:
        """Processa um documento específico."""

def main() -> None:
    """Entry point: argparse + inicialização de dependências."""
```

**Uso:**
```bash
# Processar todos os pendentes
python -m app.adapters.inbound.cli.document_ingester --run

# Processar com mais concorrência
python -m app.adapters.inbound.cli.document_ingester --run --concurrency 5

# Ver status
python -m app.adapters.inbound.cli.document_ingester --status

# Reprocessar erros
python -m app.adapters.inbound.cli.document_ingester --reprocess

# Processar URL específica
python -m app.adapters.inbound.cli.document_ingester --url https://...pdf
```

**Output esperado:**
```
=== Ingestão de Documentos ===
Pendentes: 122
Processando com concorrência: 3

[  1/122] ✓ tre-pi-diarias-junho-2022.csv (3 chunks, 1 tabela)
[  2/122] ✓ resolucao-123.pdf (12 chunks, 0 tabelas)
[  3/122] ✗ anexo-vii.csv → Erro: encoding não suportado
...

=== Resultado ===
Processados: 118/122
Erros: 4
Chunks gerados: 1.847
Tabelas extraídas: 89
Duração: 4m 32s
```

**Testes:**
- `tests/unit/test_document_ingester_cli.py` — argumentos parseados corretamente, output formatado

**Critério de aceite:**
- CLI executa pipeline completo
- Progress é exibido em tempo real
- Erros não interrompem o processamento do lote

---

### Etapa 6 — Integração com FastAPI (endpoint admin)
**Objetivo:** Endpoint REST para disparar ingestão e consultar status.

**Arquivo:** `app/adapters/inbound/fastapi/admin_router.py`

```python
router = APIRouter(prefix="/api/admin", tags=["admin"])

@router.post("/ingest")
async def trigger_ingestion(concurrency: int = 3) -> IngestionStats

@router.get("/ingest/status")
async def ingestion_status() -> IngestionStatus

@router.post("/ingest/reprocess")
async def reprocess_errors() -> IngestionStats

@router.post("/ingest/{document_url:path}")
async def ingest_single(document_url: str) -> dict
```

**Arquivo:** `app/adapters/inbound/fastapi/app.py`
- Registrar `admin_router` no app
- Injetar `DocumentIngestionService` no lifespan

**Testes:**
- `tests/integration/test_admin_endpoints.py` — endpoints retornam status corretos

**Critério de aceite:**
- `POST /api/admin/ingest` dispara pipeline
- `GET /api/admin/ingest/status` retorna contagens
- Endpoint protegido (apenas rede interna/admin)

---

### Etapa 7 — Limpeza de dados e normalização de categorias
**Objetivo:** Melhorar a qualidade dos dados existentes.

**Arquivo:** `scripts/normalize_data.py`

**Ações:**
1. Remover 182 páginas sem categoria e sem conteúdo (lixo do crawling)
2. Normalizar categorias duplicadas:
   ```
   "Orcamento E Despesas" → "Gestão Orçamentária e Financeira"
   "Tecnologia Da Informacao E Comunicacao" → "Tecnologia da Informação"
   "Contratos" → "Licitações, Contratos e Instrumentos de Cooperação"
   "Convenios" → "Licitações, Contratos e Instrumentos de Cooperação"
   "Instrumentos De Cooperacao" → "Licitações, Contratos e Instrumentos de Cooperação"
   ...etc (mapear todas as 37 para ~18 canônicas)
   ```
3. Regenerar `search_vector` das páginas afetadas (trigger faz automaticamente no UPDATE)

**Testes:**
- `tests/integration/test_normalize_data.py` — categorias consolidadas, páginas lixo removidas

**Critério de aceite:**
- De 37 categorias → ~18 categorias normalizadas
- Páginas sem conteúdo removidas (ou marcadas como inativas)
- `search_vector` atualizado

---

### Etapa 8 — Testes end-to-end e validação
**Objetivo:** Garantir que o pipeline completo funciona e o chatbot responde melhor.

**Testes:**
1. `tests/e2e/test_document_pipeline.py`
   - Dado um documento pendente → pipeline baixa, processa e persiste
   - `search_chunks()` retorna resultados relevantes
   - `search_tables()` retorna tabelas extraídas
   - ChatService.process_message() inclui citações de documentos na resposta

2. `tests/e2e/test_chat_with_documents.py`
   - Pergunta: "Quais são os estagiários do TRE-PI?"
   - Resposta deve conter dados da tabela CSV de estagiários
   - Citações devem apontar para o documento fonte

**Critério de aceite:**
- Chat responde com dados extraídos de PDFs e CSVs
- Citações incluem título do documento e snippet relevante
- Tabelas são renderizadas na resposta quando pertinente

---

## Dependências entre Etapas

```
Etapa 0 (bootstrap / deploy do zero)
    │
    │   Pode ser feita em paralelo com as etapas 1-6,
    │   mas a versão final do entrypoint depende da Etapa 5 (CLI ingester).
    │
    ├──→ Containerfile + docker-compose.yml (independente)
    ├──→ Lifespan validation no app.py (independente)
    └──→ Manifests OpenShift (independente)

Etapa 1 (migration + ports)
    │
    ├──→ Etapa 2 (download gateway)
    │         │
    ├──→ Etapa 3 (repo extensions)
    │         │
    │         ▼
    └──→ Etapa 4 (ingestion service) ← depende de 1, 2, 3
              │
              ├──→ Etapa 5 (CLI)
              │         │
              │         └──→ Etapa 0 (versão final do entrypoint com ingester)
              │
              └──→ Etapa 6 (API endpoint)

Etapa 7 (normalização) ← independente, pode ser feita em paralelo

Etapa 8 (E2E) ← depende de 4, 5 ou 6, e 0
```

**Paralelismo possível:**
- Etapa 0 (parcial: Containerfile, docker-compose, OpenShift) pode começar imediatamente
- Etapas 2 e 3 podem ser feitas em paralelo (ambas dependem apenas da 1)
- Etapa 7 pode ser feita a qualquer momento
- Etapas 5 e 6 podem ser feitas em paralelo (ambas dependem apenas da 4)
- A versão final da Etapa 0 (entrypoint com ingester) depende da Etapa 5

---

## Estimativa de Volume

| Métrica | Valor estimado |
|---|---|
| Documentos a processar | 122 (91 PDFs + 31 CSVs) |
| Chunks estimados (500 tokens, overlap 50) | ~1.500–3.000 |
| Tabelas estimadas | ~80–150 |
| Tamanho do download total | ~50–200 MB |
| Tempo de processamento (concorrência 3) | ~5–15 minutos |

---

## Considerações sobre Atualização de Dados (Upsert)

### Comportamento atual do crawler

O `upsert_page()` em `page_repo.py` usa `ON CONFLICT (url) DO UPDATE SET` com **todos os campos**. Isso significa:

| Tabela | Estratégia | Efeito |
|---|---|---|
| `pages` | `DO UPDATE SET` (todos os campos) | **Sobrescreve** título, conteúdo, categoria, etc. |
| `documents` | `DO UPDATE SET` (título, tipo, contexto) | **Sobrescreve** metadados do documento |
| `page_links` | `DO NOTHING` | Mantém existentes, insere novos, **não remove obsoletos** |
| `navigation_tree` | `DO UPDATE SET` (child_title) | **Sobrescreve** apenas o título |

### Implicações

1. **Dados do crawler são sempre sobrescritos** — não há comparação de `last_modified`. Cada execução do `--update` re-extrai e regrava todas as páginas do sitemap, mesmo que não tenham mudado.

2. **Documentos já processados (chunks/tabelas) não são afetados** — o upsert atua na tabela `documents` (metadados), não em `document_contents`/`document_chunks`/`document_tables`. Porém, se o crawler detectar um documento novo na mesma página, ele será inserido com `processing_status = 'pending'` e processado na próxima execução do ingester.

3. **Links obsoletos acumulam** — `page_links` usa `DO NOTHING`, então links que foram removidos do portal continuam no banco. Não há rotina de limpeza.

### Melhorias recomendadas (pós-pipeline)

Estas melhorias não bloqueiam a implementação do pipeline, mas devem ser consideradas para produção:

1. **Atualização condicional por `last_modified`:**
   - O sitemap do Plone já fornece `<lastmod>` em cada URL
   - Comparar com `pages.extracted_at` antes de re-extrair
   - Benefício: reduz carga no portal e no banco em ~90% nas atualizações

2. **Limpeza de links obsoletos:**
   - Antes do upsert de uma página, deletar seus `page_links` e reinserir
   - Ou: marcar links com `crawled_at` e limpar os antigos periodicamente

3. **Re-ingestão de documentos alterados:**
   - Se o crawler detectar que `documents.document_url` já existe mas o arquivo no portal mudou (via `Content-Length` ou `ETag`), resetar `processing_status` para `'pending'`
   - Benefício: mantém chunks/tabelas atualizados com a versão mais recente do documento

4. **Proteção contra sobrescrita manual:**
   - Adicionar coluna `manually_edited` (boolean) em `pages`
   - O upsert ignora páginas com `manually_edited = true`
   - Benefício: permite ajustes manuais de categoria/título que sobrevivem ao crawler

---

## Riscos e Mitigações

| Risco | Mitigação |
|---|---|
| PDFs são imagens (scanned) sem texto extraível | Log warning, marcar como `error` com mensagem "sem texto" |
| CSVs com encoding diferente (latin-1, windows-1252) | Tentar UTF-8, fallback para latin-1, fallback para error |
| Documentos muito grandes (> 50MB) | Limite configurável, skip com log |
| Portal TRE-PI indisponível durante ingestão | Retry com backoff, status 'error' preserva progresso |
| Chunks muito longos para contexto do LLM | TextChunker já controla (500 tokens padrão) |
| Tabelas com formatação inconsistente | CsvProcessor já trata; PDFs terão best-effort |
| Duplicatas entre page_content e document_content | `search_chunks()` retorna resultados distintos, LLM deduplicará no prompt |
| Crawler sobrescreve dados a cada execução | Sem impacto nos chunks/tabelas; considerar filtro por `last_modified` (melhoria futura) |
| Links obsoletos acumulam no banco | Limpeza periódica ou delete+reinsert por página (melhoria futura) |

---

## Checklist de Validação Final

### Bootstrap (Etapa 0)
- [ ] `docker-compose up` em ambiente limpo sobe sistema funcional com dados
- [ ] Entrypoint aguarda PostgreSQL antes de continuar
- [ ] `alembic upgrade head` é executado automaticamente
- [ ] Crawler roda se banco estiver vazio; é ignorado se já tem dados
- [ ] Crawler `--update` não destrói chunks/tabelas já processados
- [ ] Lifespan do FastAPI rejeita startup se schema não existe
- [ ] Variáveis de ambiente usam prefixo `CRISTAL_` consistente
- [ ] Manifests OpenShift deployam corretamente com `oc apply -f openshift/`
- [ ] InitContainer executa migrations antes do app subir
- [ ] CronJobs de crawler e ingester estão configurados

### Pipeline de Ingestão (Etapas 1-6)
- [ ] `alembic upgrade head` aplica migration 002 sem erro
- [ ] `python -m app.adapters.inbound.cli.document_ingester --status` mostra 122 pendentes
- [ ] `python -m app.adapters.inbound.cli.document_ingester --run` processa documentos
- [ ] `document_contents` tem registros com `processing_status = 'done'`
- [ ] `document_chunks` tem chunks com `search_vector` populado
- [ ] `document_tables` tem tabelas extraídas dos CSVs
- [ ] `GET /api/admin/ingest/status` retorna contagens corretas

### Qualidade de Dados (Etapa 7)
- [ ] Categorias normalizadas de 37 para ~18
- [ ] Páginas sem conteúdo removidas/inativadas

### Validação End-to-End (Etapa 8)
- [ ] Chat responde "Quais estagiários do TRE-PI?" com dados da tabela
- [ ] Chat responde "O que diz a resolução X?" com trechos do PDF
- [ ] Citações incluem documento fonte e snippet
- [ ] Todos os testes passam: `pytest`
