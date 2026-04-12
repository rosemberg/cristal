# Plano de Implementação — Pipeline de Ingestão de Documentos e Bootstrap (V2)

## Changelog V1 → V2

### Inconsistencias encontradas no V1

| # | Inconsistencia | Impacto |
|---|---|---|
| 1 | **`DocumentProcessGateway` referenciado na Etapa 4 mas nunca definido** — o construtor do `DocumentIngestionService` recebe `processor: DocumentProcessGateway`, mas nenhuma etapa cria esse port. O código existente tem `DocumentProcessor` (classe concreta), não um gateway abstrato. | Build quebra na Etapa 4 |
| 2 | **Admin router conflita com analytics_router** — o plano cria `admin_router.py` com `prefix="/api/admin"`, mas `analytics_router.py` já usa esse mesmo prefixo. | Conflito de rotas no FastAPI |
| 3 | **Sem tabela de inconsistencias** — links quebrados, páginas 404, documentos corrompidos, e recursos com problemas não são rastreados. O `processing_error` na tabela `documents` é insuficiente: cobre apenas erros de ingestão, não inconsistencias de dados em geral (links obsoletos, páginas sem conteúdo, etc.). | Admin não consegue auditar a saúde dos dados |
| 4 | **Sem validação de integridade de links** — `page_links` usa `DO NOTHING`, links removidos do portal acumulam, e não há rotina de verificação. O plano menciona isso como "melhoria futura" mas é crítico para qualidade dos dados. | Dados desatualizados sem visibilidade |
| 5 | **Sem verificação de acessibilidade de páginas/documentos** — nenhuma validação periódica se as URLs cadastradas ainda retornam 200. | Links mortos no chatbot |
| 6 | **DSN replacement no entrypoint é frágil** — `dsn.replace('postgresql+asyncpg://', 'postgresql://')` assume formato exato da URL. Se usar `postgres://` ou outro driver, falha silenciosamente. | Bootstrap pode falhar em ambientes diferentes |
| 7 | **Sem rollback de chunks parciais** — se o processamento falha no meio (ex: 5 de 12 chunks salvos), os chunks parciais permanecem no banco sem o documento ser marcado como `done`. | Dados inconsistentes na busca |
| 8 | **Sem autenticação nos endpoints admin** — o plano menciona "apenas rede interna/admin" mas não define mecanismo. | Endpoint exposto sem proteção |
| 9 | **Etapa 7 (normalização) não tem mapeamento completo** — lista "...etc (mapear todas as 37 para ~18 canônicas)" sem definir o mapeamento real. | Implementação ambígua |
| 10 | **Migration 002 assume nome fixo** — o Alembic usa revision IDs hash, não sequenciais. O nome `002_` é convencional mas o `down_revision` precisa apontar para o hash real da 001. | Migration pode não encadear |

### Melhorias adicionadas na V2

1. **Tabela `data_inconsistencies`** — registro centralizado de todos os problemas encontrados em links, páginas e documentos
2. **Etapa de Health Check** — validação periódica de acessibilidade de URLs (páginas e documentos)
3. **Port `DocumentProcessGateway`** — definição explícita do port que faltava
4. **Transação atômica para chunks** — save_content envolto em transação com rollback
5. **Limpeza de links obsoletos** — delete+reinsert por página no crawler
6. **API de inconsistencias para o admin** — endpoints para consultar/resolver problemas
7. **Autenticação básica** — API key para endpoints admin

---

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
| `analytics_router.py` | Existe com prefix `/api/admin` |

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

## Arquitetura da Pipeline (V2)

```
┌──────────────────────────────────────────────────────────────┐
│                    CLI: document_ingester.py                  │
│                                                              │
│  python -m app.adapters.inbound.cli.document_ingester        │
│    --run       Processa todos os documentos pendentes        │
│    --reprocess Reprocessa documentos com erro                │
│    --status    Mostra estatísticas de processamento          │
│    --url URL   Processa um único documento                   │
│    --check     Verifica saúde de URLs (links, páginas, docs) │
│    --inconsistencies  Lista inconsistencias pendentes        │
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
│    5. Registrar inconsistencias detectadas                   │
└──────┬──────────┬──────────┬──────────┬──────────────────────┘
       │          │          │          │
       ▼          ▼          ▼          ▼
  HttpClient  DocProcessor  DocRepo   InconsistencyRepo
  (download)  (extração)    (save)    (problemas)

┌──────────────────────────────────────────────────────────────┐
│              DataHealthCheckService (domínio)                 │
│                                                              │
│  Porta de entrada: DataHealthCheckUseCase (ABC)              │
│                                                              │
│  Responsabilidades:                                          │
│    1. Verificar acessibilidade de URLs (páginas e docs)      │
│    2. Detectar links quebrados em page_links                 │
│    3. Detectar documentos sem conteúdo extraído              │
│    4. Registrar problemas na tabela data_inconsistencies     │
│    5. Gerar relatório de saúde dos dados                     │
└──────┬──────────┬──────────┬─────────────────────────────────┘
       │          │          │
       ▼          ▼          ▼
  HttpClient  PageRepo   InconsistencyRepo
  (HEAD req)  (consulta)  (registra)
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

   # 1. Aguardar PostgreSQL (com timeout de 60s)
   echo "[1/4] Aguardando PostgreSQL..."
   RETRIES=30
   until python -c "
   import asyncio, asyncpg, os
   async def check():
       dsn = os.environ.get('CRISTAL_DATABASE_URL', '')
       # Suporta postgresql://, postgresql+asyncpg://, postgres://
       for prefix in ['postgresql+asyncpg://', 'postgres://']:
           dsn = dsn.replace(prefix, 'postgresql://')
       await asyncpg.connect(dsn, timeout=5)
   asyncio.run(check())
   " 2>/dev/null; do
       RETRIES=$((RETRIES - 1))
       if [ "$RETRIES" -le 0 ]; then
           echo "ERRO: PostgreSQL não disponível após 60s. Abortando."
           exit 1
       fi
       echo "  PostgreSQL indisponível, tentando novamente em 2s... ($RETRIES restantes)"
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
       for prefix in ['postgresql+asyncpg://', 'postgres://']:
           dsn = dsn.replace(prefix, 'postgresql://')
       conn = await asyncpg.connect(dsn)
       try:
           n = await conn.fetchval('SELECT COUNT(*) FROM pages')
       except Exception:
           n = 0
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
         CRISTAL_ADMIN_API_KEY: ${ADMIN_API_KEY:-dev-admin-key-change-me}
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
     CRISTAL_ADMIN_API_KEY: "CHAVE_ADMIN_PRODUCAO"
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

   `openshift/cronjob-healthcheck.yaml` — **[NOVO V2]** Verificação periódica de saúde dos dados
   ```yaml
   apiVersion: batch/v1
   kind: CronJob
   metadata:
     name: cristal-healthcheck
   spec:
     schedule: "0 4 * * 0"  # Domingos às 4h (após ingester)
     jobTemplate:
       spec:
         template:
           spec:
             containers:
               - name: healthcheck
                 image: cristal:latest
                 command:
                   - python
                   - -m
                   - app.adapters.inbound.cli.document_ingester
                   - --check
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
  - Timeout de 60s atingido → entrypoint aborta com erro claro
- `tests/unit/test_docker_compose_env.py`
  - Variáveis usam prefixo `CRISTAL_` consistente
  - Credenciais montam no path correto

**Critério de aceite:**
- `docker-compose up` em ambiente limpo resulta em sistema funcional com dados
- `oc apply -f openshift/` deploya no OpenShift com migrations automáticas
- CronJobs mantêm dados atualizados semanalmente (crawler + ingester + healthcheck)
- App **não** sobe se schema não existe (fail-fast)
- Entrypoint falha com mensagem clara se PostgreSQL não responde em 60s

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
       ├─ [1] Aguarda PostgreSQL (timeout 60s) ✓
       ├─ [2] alembic upgrade head (cria tabelas + views + triggers)
       ├─ [3] Banco vazio? → crawler --full (890 páginas + 122 docs + links)
       ├─ [4] document_ingester --run (processa 122 PDFs/CSVs → chunks + tabelas)
       └─ [5] uvicorn app.main:app (sistema 100% funcional)
```

---

### Etapa 1 — Port de ingestão, status de processamento e tabela de inconsistencias
**Objetivo:** Definir a interface do serviço, adicionar coluna de controle na tabela `documents`, e criar a tabela centralizada de inconsistencias.

**Arquivos:**

1. `migrations/versions/002_document_processing_and_inconsistencies.py`

   **Alterações na tabela `documents`:**
   - Adicionar coluna `processing_status` (`pending | processing | done | error`)
   - Adicionar coluna `processing_error` (TEXT, nullable)
   - Adicionar coluna `processed_at` (TIMESTAMPTZ, nullable)
   - DEFAULT: `'pending'` para registros existentes
   - Índice: `idx_documents_processing_status`

   **Nova tabela `data_inconsistencies`:**
   ```sql
   CREATE TABLE data_inconsistencies (
       id SERIAL PRIMARY KEY,

       -- Classificação do problema
       resource_type VARCHAR(20) NOT NULL,
       -- 'page' | 'document' | 'link' | 'chunk'

       severity VARCHAR(10) NOT NULL DEFAULT 'warning',
       -- 'critical' | 'warning' | 'info'

       inconsistency_type VARCHAR(50) NOT NULL,
       -- Tipos possíveis:
       --   'broken_link'        → link retorna 404/500/timeout
       --   'page_not_accessible'→ página cadastrada retorna erro HTTP
       --   'document_not_found' → documento cadastrado não encontrado na URL
       --   'document_corrupted' → documento existe mas não pode ser processado
       --   'empty_content'      → página/documento sem conteúdo extraível
       --   'encoding_error'     → CSV/página com encoding inválido
       --   'oversized'          → documento excede limite de tamanho
       --   'orphan_chunks'      → chunks sem documento pai válido
       --   'duplicate_content'  → conteúdo duplicado entre recursos
       --   'category_mismatch'  → categoria atribuída incorretamente
       --   'missing_metadata'   → metadados obrigatórios ausentes

       -- Recurso afetado
       resource_url TEXT NOT NULL,
       resource_title TEXT,
       parent_page_url TEXT,        -- página que referencia o recurso (se aplicável)

       -- Detalhes do problema
       detail TEXT NOT NULL,        -- descrição legível do problema
       http_status INTEGER,         -- status HTTP retornado (se aplicável)
       error_message TEXT,          -- mensagem técnica do erro

       -- Rastreamento
       detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
       detected_by VARCHAR(50) NOT NULL,
       -- 'ingestion_pipeline' | 'health_check' | 'crawler' | 'manual'

       -- Resolução
       status VARCHAR(20) NOT NULL DEFAULT 'open',
       -- 'open' | 'acknowledged' | 'resolved' | 'ignored'
       resolved_at TIMESTAMPTZ,
       resolved_by VARCHAR(100),
       resolution_note TEXT,

       -- Controle
       retry_count INTEGER NOT NULL DEFAULT 0,
       last_checked_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
   );

   -- Índices para consultas do admin
   CREATE INDEX idx_inconsistencies_status ON data_inconsistencies(status);
   CREATE INDEX idx_inconsistencies_type ON data_inconsistencies(inconsistency_type);
   CREATE INDEX idx_inconsistencies_severity ON data_inconsistencies(severity);
   CREATE INDEX idx_inconsistencies_resource ON data_inconsistencies(resource_url);
   CREATE INDEX idx_inconsistencies_detected ON data_inconsistencies(detected_at DESC);

   -- Índice composto para consulta principal do dashboard
   CREATE INDEX idx_inconsistencies_open_severity
       ON data_inconsistencies(status, severity, detected_at DESC)
       WHERE status = 'open';
   ```

   **Nota sobre migration:** O `down_revision` deve apontar para o hash real gerado pelo Alembic na migration 001 (verificar com `alembic history`).

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

3. `app/domain/ports/inbound/data_health_check_use_case.py` — **[NOVO V2]**
   ```python
   class DataHealthCheckUseCase(ABC):
       @abstractmethod
       async def check_pages(self, concurrency: int = 5) -> HealthCheckReport

       @abstractmethod
       async def check_documents(self, concurrency: int = 5) -> HealthCheckReport

       @abstractmethod
       async def check_links(self, concurrency: int = 10) -> HealthCheckReport

       @abstractmethod
       async def check_all(self, concurrency: int = 5) -> HealthCheckReport

       @abstractmethod
       async def get_inconsistencies(
           self,
           status: str = "open",
           resource_type: str | None = None,
           severity: str | None = None,
           limit: int = 50,
           offset: int = 0,
       ) -> list[DataInconsistency]

       @abstractmethod
       async def resolve_inconsistency(
           self, inconsistency_id: int, resolution_note: str, resolved_by: str
       ) -> None

       @abstractmethod
       async def acknowledge_inconsistency(self, inconsistency_id: int) -> None

       @abstractmethod
       async def ignore_inconsistency(
           self, inconsistency_id: int, reason: str
       ) -> None
   ```

4. `app/domain/value_objects/ingestion.py`
   ```python
   @dataclass(frozen=True)
   class IngestionStats:
       total: int
       processed: int
       errors: int
       skipped: int
       duration_seconds: float
       inconsistencies_found: int  # [NOVO V2]

   @dataclass(frozen=True)
   class IngestionStatus:
       pending: int
       processing: int
       done: int
       error: int
       total_chunks: int
       total_tables: int
       open_inconsistencies: int  # [NOVO V2]
   ```

5. `app/domain/value_objects/data_inconsistency.py` — **[NOVO V2]**
   ```python
   @dataclass(frozen=True)
   class DataInconsistency:
       id: int | None
       resource_type: str     # 'page' | 'document' | 'link' | 'chunk'
       severity: str          # 'critical' | 'warning' | 'info'
       inconsistency_type: str
       resource_url: str
       resource_title: str | None
       parent_page_url: str | None
       detail: str
       http_status: int | None
       error_message: str | None
       detected_at: datetime
       detected_by: str
       status: str            # 'open' | 'acknowledged' | 'resolved' | 'ignored'
       resolved_at: datetime | None
       resolved_by: str | None
       resolution_note: str | None
       retry_count: int
       last_checked_at: datetime

   @dataclass(frozen=True)
   class HealthCheckReport:
       total_checked: int
       healthy: int
       issues_found: int
       new_inconsistencies: int
       updated_inconsistencies: int
       duration_seconds: float
       by_type: dict[str, int]  # contagem por inconsistency_type
   ```

**Testes:**
- `tests/unit/test_ingestion_value_objects.py` — criação e imutabilidade dos VOs
- `tests/unit/test_data_inconsistency_vo.py` — criação e validação dos VOs de inconsistencia
- `tests/integration/test_migration_002.py` — migração cria colunas, tabela e índices

**Critério de aceite:**
- `alembic upgrade head` aplica a migração sem erro
- 122 documentos existentes ganham `processing_status = 'pending'`
- Tabela `data_inconsistencies` criada com todos os índices
- Migration encadeia corretamente com 001 (down_revision verificado)

---

### Etapa 2 — Gateway de download HTTP e Port de processamento
**Objetivo:** Criar adapter para baixar documentos do portal TRE-PI com retry e timeout. **[V2: também definir o port `DocumentProcessGateway` que faltava no V1.]**

**Arquivos:**

1. `app/domain/ports/outbound/document_download_gateway.py`
   ```python
   class DocumentDownloadGateway(ABC):
       @abstractmethod
       async def download(self, url: str) -> DownloadResult

       @abstractmethod
       async def check_accessible(self, url: str) -> AccessCheckResult
       # [NOVO V2] HEAD request para verificar acessibilidade

   @dataclass(frozen=True)
   class DownloadResult:
       content: bytes
       content_type: str
       size_bytes: int
       status_code: int

   @dataclass(frozen=True)
   class AccessCheckResult:  # [NOVO V2]
       url: str
       accessible: bool
       status_code: int
       content_type: str | None
       content_length: int | None
       error: str | None
       response_time_ms: float
   ```

2. `app/domain/ports/outbound/document_process_gateway.py` — **[NOVO V2 — CORRIGE INCONSISTENCIA #1]**
   ```python
   from app.domain.ports.outbound.document_repository import ProcessedDocument

   class DocumentProcessGateway(ABC):
       """Port que abstrai o processamento de documentos (PDF, CSV, etc)."""
       @abstractmethod
       async def process(
           self, url: str, content: bytes, doc_type: str
       ) -> ProcessedDocument
   ```

3. `app/adapters/outbound/document_processor/document_process_adapter.py` — **[NOVO V2]**
   ```python
   class DocumentProcessAdapter(DocumentProcessGateway):
       """Adapter que encapsula o DocumentProcessor existente no port."""
       def __init__(self, processor: DocumentProcessor) -> None:
           self._processor = processor

       async def process(
           self, url: str, content: bytes, doc_type: str
       ) -> ProcessedDocument:
           return self._processor.process(url, content, doc_type)
   ```

4. `app/adapters/outbound/http/document_downloader.py`
   ```python
   class HttpDocumentDownloader(DocumentDownloadGateway):
       def __init__(self, timeout: float = 30.0, max_retries: int = 2)
       async def download(self, url: str) -> DownloadResult
       async def check_accessible(self, url: str) -> AccessCheckResult
   ```
   - Restrição de domínio: apenas `tre-pi.jus.br`
   - Timeout: 30s por documento
   - Retry: 2 tentativas com backoff exponencial (1s, 2s)
   - Limite de tamanho: 50MB (settings.max_document_size_mb)
   - User-Agent institucional
   - `check_accessible`: HEAD request com timeout de 10s, sem retry

**Testes:**
- `tests/unit/test_document_downloader.py` — mock httpx, testa retry, timeout, domain check, size limit
- `tests/unit/test_document_process_adapter.py` — adapter delega corretamente ao processor
- `tests/unit/test_access_check.py` — HEAD request retorna resultado correto

**Critério de aceite:**
- Download de PDF e CSV do portal TRE-PI funciona
- URLs fora de `tre-pi.jus.br` são rejeitadas
- Documentos > 50MB são ignorados com log
- `check_accessible` retorna resultado sem baixar o conteúdo inteiro
- `DocumentProcessGateway` definido como port abstrato

---

### Etapa 3 — Extensão do DocumentRepository e InconsistencyRepository
**Objetivo:** Adicionar métodos para controle de status no repository. **[V2: adicionar repository de inconsistencias.]**

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

   @abstractmethod
   async def save_content_atomic(
       self, document_url: str, content: ProcessedDocument
   ) -> None
   # [NOVO V2] Salva chunks e tabelas em transação única — rollback se falhar
   ```

2. `app/adapters/outbound/postgres/document_repo.py` — implementar:
   ```python
   async def list_pending(self, limit: int = 50) -> list[Document]:
       # SELECT ... FROM documents WHERE processing_status = 'pending'
       # ORDER BY created_at ASC LIMIT $1

   async def list_errors(self) -> list[Document]:
       # SELECT ... FROM documents WHERE processing_status = 'error'

   async def update_status(
       self, document_url: str, status: str, error: str | None = None
   ) -> None:
       # UPDATE documents SET processing_status=$2, processing_error=$3,
       #   processed_at=NOW() WHERE document_url = $1

   async def save_content_atomic(
       self, document_url: str, content: ProcessedDocument
   ) -> None:
       # [NOVO V2] Transação explícita:
       # BEGIN
       #   DELETE FROM document_chunks WHERE document_url = $1
       #   DELETE FROM document_tables WHERE document_url = $1
       #   DELETE FROM document_contents WHERE document_url = $1
       #   INSERT INTO document_contents ...
       #   INSERT INTO document_chunks ... (batch)
       #   INSERT INTO document_tables ... (batch)
       # COMMIT
       # Em caso de erro: ROLLBACK → nenhum dado parcial persiste
   ```

3. `app/domain/ports/outbound/inconsistency_repository.py` — **[NOVO V2]**
   ```python
   class InconsistencyRepository(ABC):
       @abstractmethod
       async def save(self, inconsistency: DataInconsistency) -> int:
           """Salva ou atualiza inconsistencia. Retorna o ID."""

       @abstractmethod
       async def upsert(
           self, resource_url: str, inconsistency_type: str,
           inconsistency: DataInconsistency
       ) -> int:
           """Atualiza se já existe (mesmo URL + tipo), ou cria nova."""

       @abstractmethod
       async def list_by_status(
           self, status: str = "open",
           resource_type: str | None = None,
           severity: str | None = None,
           limit: int = 50, offset: int = 0
       ) -> list[DataInconsistency]:
           """Lista inconsistencias filtradas."""

       @abstractmethod
       async def count_by_status(self) -> dict[str, int]:
           """Retorna contagem por status: {open: N, acknowledged: N, ...}"""

       @abstractmethod
       async def count_by_type(self, status: str = "open") -> dict[str, int]:
           """Retorna contagem por tipo de inconsistencia."""

       @abstractmethod
       async def update_status(
           self, inconsistency_id: int, status: str,
           resolved_by: str | None = None, resolution_note: str | None = None
       ) -> None:

       @abstractmethod
       async def mark_resolved_by_url(
           self, resource_url: str, inconsistency_type: str,
           resolution_note: str
       ) -> int:
           """Resolve todas as inconsistencias de um recurso+tipo. Retorna qtd."""

       @abstractmethod
       async def get_summary(self) -> InconsistencySummary:
           """Resumo para o dashboard admin."""
   ```

4. `app/domain/value_objects/inconsistency_summary.py` — **[NOVO V2]**
   ```python
   @dataclass(frozen=True)
   class InconsistencySummary:
       total_open: int
       total_acknowledged: int
       total_resolved: int
       total_ignored: int
       by_severity: dict[str, int]      # {critical: N, warning: N, info: N}
       by_type: dict[str, int]          # {broken_link: N, ...}
       by_resource_type: dict[str, int] # {page: N, document: N, link: N}
       oldest_open: datetime | None
       last_check: datetime | None
   ```

5. `app/adapters/outbound/postgres/inconsistency_repo.py` — **[NOVO V2]**
   - Implementação PostgreSQL do `InconsistencyRepository`
   - Usa `ON CONFLICT (resource_url, inconsistency_type) WHERE status = 'open'` para upsert inteligente
   - Incrementa `retry_count` e atualiza `last_checked_at` em re-detecções

**Testes:**
- `tests/integration/test_document_repo_status.py` — list_pending, update_status, list_errors com banco real
- `tests/integration/test_document_repo_atomic.py` — save_content_atomic com rollback em falha
- `tests/integration/test_inconsistency_repo.py` — CRUD completo, upsert, contagens

**Critério de aceite:**
- `list_pending()` retorna documentos com status 'pending'
- `update_status()` atualiza status e timestamp
- Status inválido lança exceção
- `save_content_atomic()` não deixa chunks parciais em caso de erro
- `InconsistencyRepository` persiste e consulta inconsistencias corretamente
- Upsert não duplica inconsistencias do mesmo recurso+tipo

---

### Etapa 4 — DocumentIngestionService (domínio)
**Objetivo:** Serviço de domínio que orquestra o pipeline completo. **[V2: usa `DocumentProcessGateway` e registra inconsistencias.]**

**Arquivo:** `app/domain/services/document_ingestion_service.py`

```python
class DocumentIngestionService(DocumentIngestionUseCase):
    def __init__(
        self,
        doc_repo: DocumentRepository,
        downloader: DocumentDownloadGateway,
        processor: DocumentProcessGateway,  # [V2] Port, não classe concreta
        inconsistency_repo: InconsistencyRepository,  # [NOVO V2]
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
           d. doc_repo.save_content_atomic(url, processed_doc)  # [V2] Transação
           e. doc_repo.update_status(url, 'done')
           f. Em caso de erro:
              - doc_repo.update_status(url, 'error', str(e))
              - inconsistency_repo.upsert(...)  # [V2] Registra inconsistencia
        3. Retorna IngestionStats (com inconsistencies_found)
        """

    async def ingest_single(self, document_url: str) -> bool:
        """Processa um único documento por URL."""

    async def reprocess_errors(self) -> IngestionStats:
        """Reseta status de 'error' para 'pending' e executa ingest_pending."""

    async def get_status(self) -> IngestionStatus:
        """Retorna contagens por status + totais de chunks/tables + open_inconsistencies."""
```

**Classificação automática de inconsistencias durante ingestão:**

| Erro detectado | `inconsistency_type` | `severity` |
|---|---|---|
| HTTP 404 no download | `document_not_found` | `critical` |
| HTTP 500/502/503 no download | `document_not_found` | `warning` |
| Timeout no download | `document_not_found` | `warning` |
| PDF sem texto extraível (scanned) | `document_corrupted` | `warning` |
| CSV com encoding inválido | `encoding_error` | `warning` |
| Documento > 50MB | `oversized` | `info` |
| Nenhum chunk gerado | `empty_content` | `critical` |
| Exceção inesperada | `document_corrupted` | `critical` |

**Fluxo de um documento:**

```
                ┌─ update_status('processing') ─┐
                │                                │
list_pending() ─┤   download(url)                │
                │        │                       │
                │   process(url, bytes, type)     │
                │        │                       │
                │   save_content_atomic(url, res) │  ← [V2] transação
                │        │                       │
                │   update_status('done')  ───────┘
                │                                │
                └── SE ERRO:                     │
                    update_status('error', msg)  │
                    inconsistency_repo.upsert()  │  ← [V2] registra
```

**Testes:**
- `tests/unit/test_document_ingestion_service.py`
  - Fluxo feliz: pending → processing → done
  - Erro de download: status muda para 'error' com mensagem + inconsistencia registrada
  - Erro de processamento: status muda para 'error' + inconsistencia registrada
  - Concorrência: Semaphore limita execuções paralelas
  - `ingest_single`: processa documento específico
  - `reprocess_errors`: reseta e reprocessa
  - `get_status`: retorna contagens corretas (incluindo inconsistencias)
  - PDF sem texto: inconsistencia `document_corrupted` com severity `warning`
  - Documento > 50MB: inconsistencia `oversized` com severity `info`

**Critério de aceite:**
- Pipeline processa documento end-to-end (download → chunking → persistência)
- Erros são capturados e registrados sem interromper o lote
- Status é atualizado atomicamente em cada transição
- **Cada erro gera um registro em `data_inconsistencies`**
- `save_content_atomic` garante rollback de chunks parciais

---

### Etapa 5 — DataHealthCheckService (domínio) — **[NOVA V2]**
**Objetivo:** Serviço dedicado à verificação periódica de saúde dos dados — detecta links quebrados, páginas inacessíveis e documentos com problemas.

**Arquivo:** `app/domain/services/data_health_check_service.py`

```python
class DataHealthCheckService(DataHealthCheckUseCase):
    def __init__(
        self,
        downloader: DocumentDownloadGateway,
        page_repo: PageRepository,
        doc_repo: DocumentRepository,
        inconsistency_repo: InconsistencyRepository,
    ) -> None

    async def check_pages(self, concurrency: int = 5) -> HealthCheckReport:
        """
        Para cada página no banco:
        1. HEAD request na URL
        2. Se 404/500/timeout → upsert inconsistencia 'page_not_accessible'
        3. Se 200 e inconsistencia existente → auto-resolve
        """

    async def check_documents(self, concurrency: int = 5) -> HealthCheckReport:
        """
        Para cada documento com status 'done':
        1. HEAD request na URL do documento
        2. Se 404 → upsert inconsistencia 'document_not_found' (critical)
        3. Se Content-Length mudou → upsert inconsistencia 'document_corrupted'
           com detail "documento pode ter sido atualizado"
        """

    async def check_links(self, concurrency: int = 10) -> HealthCheckReport:
        """
        Para cada link em page_links:
        1. HEAD request na URL
        2. Se 404/timeout → upsert inconsistencia 'broken_link'
        3. Se 200 e inconsistencia existente → auto-resolve
        Nota: usa concorrência maior pois são requests HEAD leves.
        """

    async def check_all(self, concurrency: int = 5) -> HealthCheckReport:
        """Executa check_pages + check_documents + check_links, consolida report."""

    async def get_inconsistencies(self, ...) -> list[DataInconsistency]:
        """Delega para inconsistency_repo.list_by_status()."""

    async def resolve_inconsistency(self, ...) -> None:
        """Marca como resolvida com nota."""

    async def acknowledge_inconsistency(self, ...) -> None:
        """Marca como acknowledged (admin viu, vai tratar)."""

    async def ignore_inconsistency(self, ...) -> None:
        """Marca como ignored com justificativa."""
```

**Auto-resolução:** Se um recurso previamente marcado como inconsistente agora retorna 200, o serviço automaticamente resolve a inconsistencia com a nota "Auto-resolved: recurso acessível em [data]".

**Rate limiting:** Para não sobrecarregar o portal TRE-PI, as verificações usam um delay de 200ms entre requests (configurável).

**Testes:**
- `tests/unit/test_data_health_check_service.py`
  - Página acessível → nenhuma inconsistencia
  - Página 404 → inconsistencia `page_not_accessible` criada
  - Página previamente 404 agora 200 → auto-resolve
  - Link quebrado → inconsistencia `broken_link`
  - Documento com Content-Length diferente → inconsistencia `document_corrupted`
  - Rate limiting respeita delay configurado
  - `check_all` consolida reports

**Critério de aceite:**
- Health check verifica todas as URLs do sistema
- Inconsistencias são registradas com tipo, severidade e detalhes
- Recursos que voltam a funcionar são auto-resolvidos
- Rate limiting protege o portal TRE-PI

---

### Etapa 6 — CLI Adapter
**Objetivo:** Interface de linha de comando para executar a pipeline e health checks.

**Arquivo:** `app/adapters/inbound/cli/document_ingester.py`

```python
class DocumentIngesterCLI:
    def __init__(
        self,
        ingestion_service: DocumentIngestionUseCase,
        health_check_service: DataHealthCheckUseCase,  # [NOVO V2]
    ) -> None

    async def run(self, concurrency: int = 3) -> None:
        """Executa ingestão com progress bar."""

    async def reprocess(self) -> None:
        """Reprocessa documentos com erro."""

    async def status(self) -> None:
        """Imprime estatísticas formatadas (inclui inconsistencias)."""

    async def single(self, url: str) -> None:
        """Processa um documento específico."""

    async def check(self) -> None:  # [NOVO V2]
        """Executa health check completo e exibe report."""

    async def inconsistencies(self) -> None:  # [NOVO V2]
        """Lista inconsistencias pendentes com filtros."""

def main() -> None:
    """Entry point: argparse + inicialização de dependências."""
```

**Uso:**
```bash
# Processar todos os pendentes
python -m app.adapters.inbound.cli.document_ingester --run

# Processar com mais concorrência
python -m app.adapters.inbound.cli.document_ingester --run --concurrency 5

# Ver status (agora inclui inconsistencias)
python -m app.adapters.inbound.cli.document_ingester --status

# Reprocessar erros
python -m app.adapters.inbound.cli.document_ingester --reprocess

# Processar URL específica
python -m app.adapters.inbound.cli.document_ingester --url https://...pdf

# [NOVO V2] Health check completo
python -m app.adapters.inbound.cli.document_ingester --check

# [NOVO V2] Listar inconsistencias
python -m app.adapters.inbound.cli.document_ingester --inconsistencies
python -m app.adapters.inbound.cli.document_ingester --inconsistencies --severity critical
python -m app.adapters.inbound.cli.document_ingester --inconsistencies --type broken_link
```

**Output esperado (ingestão):**
```
=== Ingestão de Documentos ===
Pendentes: 122
Processando com concorrência: 3

[  1/122] ✓ tre-pi-diarias-junho-2022.csv (3 chunks, 1 tabela)
[  2/122] ✓ resolucao-123.pdf (12 chunks, 0 tabelas)
[  3/122] ✗ anexo-vii.csv → Erro: encoding não suportado
          ⚠ Inconsistencia registrada: encoding_error (warning)
...

=== Resultado ===
Processados: 118/122
Erros: 4
Chunks gerados: 1.847
Tabelas extraídas: 89
Inconsistencias registradas: 4
Duração: 4m 32s
```

**Output esperado (health check):** — **[NOVO V2]**
```
=== Health Check de Dados ===

[1/3] Verificando 890 páginas...
      ✓ 882 acessíveis
      ✗ 8 com problemas (5 broken_link, 3 page_not_accessible)
      ↻ 2 auto-resolvidas (voltaram a funcionar)

[2/3] Verificando 122 documentos...
      ✓ 119 acessíveis
      ✗ 3 com problemas (2 document_not_found, 1 oversized)

[3/3] Verificando 1.247 links...
      ✓ 1.198 acessíveis
      ✗ 49 com problemas (42 broken_link, 7 timeout)
      ↻ 5 auto-resolvidas

=== Resumo ===
Total verificado: 2.259
Novas inconsistencias: 60
Auto-resolvidas: 7
Duração: 12m 45s

Inconsistencias abertas por severidade:
  🔴 critical: 12
  🟡 warning: 38
  🔵 info: 10
```

**Output esperado (inconsistencias):** — **[NOVO V2]**
```
=== Inconsistencias Abertas ===

 ID | Tipo                  | Severidade | Recurso                          | Detectado
----|-----------------------|------------|----------------------------------|----------
 23 | document_not_found    | critical   | resolucao-456.pdf                | 2026-04-10
 45 | broken_link           | warning    | /transparencia/pagina-antiga     | 2026-04-10
 46 | encoding_error        | warning    | dados-2019.csv                   | 2026-04-11
 ...

Total: 60 abertas (12 critical, 38 warning, 10 info)
```

**Testes:**
- `tests/unit/test_document_ingester_cli.py` — argumentos parseados corretamente, output formatado

**Critério de aceite:**
- CLI executa pipeline completo
- Progress é exibido em tempo real
- Erros não interrompem o processamento do lote
- `--check` executa health check e registra inconsistencias
- `--inconsistencies` lista problemas com filtros

---

### Etapa 7 — Integração com FastAPI (endpoints admin)
**Objetivo:** Endpoints REST para disparar ingestão, consultar status e gerenciar inconsistencias. **[V2: resolve conflito com analytics_router e adiciona autenticação + endpoints de inconsistencias.]**

**Arquivo:** `app/adapters/inbound/fastapi/ingestion_router.py` — **[V2: renomeado de `admin_router.py` para evitar conflito]**

```python
router = APIRouter(prefix="/api/admin/ingestion", tags=["ingestion"])
# [V2] Prefix mais específico: /api/admin/ingestion (não conflita com analytics)

# Middleware de autenticação — [NOVO V2]
async def verify_admin_key(x_admin_key: str = Header(...)) -> None:
    """Verifica API key do admin via header X-Admin-Key."""
    if x_admin_key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")

# --- Ingestão ---

@router.post("/run", dependencies=[Depends(verify_admin_key)])
async def trigger_ingestion(concurrency: int = 3) -> IngestionStats

@router.get("/status", dependencies=[Depends(verify_admin_key)])
async def ingestion_status() -> IngestionStatus

@router.post("/reprocess", dependencies=[Depends(verify_admin_key)])
async def reprocess_errors() -> IngestionStats

@router.post("/single/{document_url:path}", dependencies=[Depends(verify_admin_key)])
async def ingest_single(document_url: str) -> dict

# --- Health Check --- [NOVO V2]

@router.post("/health-check", dependencies=[Depends(verify_admin_key)])
async def trigger_health_check(
    check_type: str = "all",  # all | pages | documents | links
    concurrency: int = 5,
) -> HealthCheckReport

# --- Inconsistencias --- [NOVO V2]

@router.get("/inconsistencies", dependencies=[Depends(verify_admin_key)])
async def list_inconsistencies(
    status: str = "open",
    resource_type: str | None = None,
    severity: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[DataInconsistency]

@router.get("/inconsistencies/summary", dependencies=[Depends(verify_admin_key)])
async def inconsistency_summary() -> InconsistencySummary

@router.patch(
    "/inconsistencies/{id}/resolve",
    dependencies=[Depends(verify_admin_key)]
)
async def resolve_inconsistency(
    id: int, resolution_note: str, resolved_by: str
) -> dict

@router.patch(
    "/inconsistencies/{id}/acknowledge",
    dependencies=[Depends(verify_admin_key)]
)
async def acknowledge_inconsistency(id: int) -> dict

@router.patch(
    "/inconsistencies/{id}/ignore",
    dependencies=[Depends(verify_admin_key)]
)
async def ignore_inconsistency(id: int, reason: str) -> dict
```

**Arquivo:** `app/adapters/inbound/fastapi/app.py`
- Registrar `ingestion_router` no app (não conflita com `analytics_router`)
- Injetar `DocumentIngestionService`, `DataHealthCheckService` e `InconsistencyRepository` no lifespan

**Arquivo:** `app/config.py` ou `app/settings.py`
- Adicionar `CRISTAL_ADMIN_API_KEY` como variável de configuração

**Testes:**
- `tests/integration/test_ingestion_endpoints.py` — endpoints retornam status corretos
- `tests/integration/test_inconsistency_endpoints.py` — CRUD de inconsistencias via API
- `tests/unit/test_admin_auth.py` — requests sem API key retornam 403

**Critério de aceite:**
- `POST /api/admin/ingestion/run` dispara pipeline
- `GET /api/admin/ingestion/status` retorna contagens
- `GET /api/admin/ingestion/inconsistencies` lista problemas
- `GET /api/admin/ingestion/inconsistencies/summary` retorna resumo
- Todos os endpoints exigem header `X-Admin-Key`
- Não conflita com rotas existentes em `analytics_router`

---

### Etapa 8 — Limpeza de dados e normalização de categorias
**Objetivo:** Melhorar a qualidade dos dados existentes. **[V2: registra inconsistencias encontradas durante normalização.]**

**Arquivo:** `scripts/normalize_data.py`

**Ações:**
1. Remover/inativar 182 páginas sem categoria e sem conteúdo (lixo do crawling)
   - **[V2]** Registrar como inconsistencia `empty_content` com `detected_by='crawler'` antes de remover
2. Normalizar categorias duplicadas:
   ```
   "Orcamento E Despesas" → "Gestão Orçamentária e Financeira"
   "Despesas" → "Gestão Orçamentária e Financeira"
   "Receitas" → "Gestão Orçamentária e Financeira"
   "Tecnologia Da Informacao E Comunicacao" → "Tecnologia da Informação"
   "Contratos" → "Licitações, Contratos e Instrumentos de Cooperação"
   "Convenios" → "Licitações, Contratos e Instrumentos de Cooperação"
   "Instrumentos De Cooperacao" → "Licitações, Contratos e Instrumentos de Cooperação"
   "Gestao De Pessoas" → "Gestão de Pessoas"
   "Servidores" → "Gestão de Pessoas"
   "Estagiarios" → "Gestão de Pessoas"
   "Planejamento E Gestao" → "Planejamento e Gestão"
   "Prestacao De Contas" → "Prestação de Contas"
   "Auditorias" → "Auditoria e Correição"
   "Corregedoria" → "Auditoria e Correição"
   "Institucional" → "Informações Institucionais"
   "Composicao" → "Informações Institucionais"
   "Legislacao" → "Legislação e Normas"
   "Resolucoes" → "Legislação e Normas"
   "Eleicoes" → "Processo Eleitoral"
   ```
   **[V2]** Mapeamento completo e explícito — sem "etc".

3. Regenerar `search_vector` das páginas afetadas (trigger faz automaticamente no UPDATE)
4. **[V2]** Registrar categorias não mapeadas como inconsistencia `category_mismatch` para revisão manual

**Testes:**
- `tests/integration/test_normalize_data.py` — categorias consolidadas, páginas lixo removidas

**Critério de aceite:**
- De 37 categorias → ~18 categorias normalizadas
- Páginas sem conteúdo removidas (ou marcadas como inativas)
- `search_vector` atualizado
- **Categorias não mapeadas registradas como inconsistencias**

---

### Etapa 9 — Testes end-to-end e validação
**Objetivo:** Garantir que o pipeline completo funciona e o chatbot responde melhor.

**Testes:**
1. `tests/e2e/test_document_pipeline.py`
   - Dado um documento pendente → pipeline baixa, processa e persiste
   - `search_chunks()` retorna resultados relevantes
   - `search_tables()` retorna tabelas extraídas
   - ChatService.process_message() inclui citações de documentos na resposta
   - **[V2]** Documento com erro → inconsistencia registrada em `data_inconsistencies`

2. `tests/e2e/test_chat_with_documents.py`
   - Pergunta: "Quais são os estagiários do TRE-PI?"
   - Resposta deve conter dados da tabela CSV de estagiários
   - Citações devem apontar para o documento fonte

3. `tests/e2e/test_health_check.py` — **[NOVO V2]**
   - Health check detecta página 404 → inconsistencia criada
   - Health check detecta link quebrado → inconsistencia criada
   - Recurso volta a funcionar → inconsistencia auto-resolvida

4. `tests/e2e/test_inconsistency_admin.py` — **[NOVO V2]**
   - Admin lista inconsistencias via API
   - Admin resolve inconsistencia → status muda para 'resolved'
   - Admin ignora inconsistencia → status muda para 'ignored' com razão
   - Dashboard mostra contagens corretas

**Critério de aceite:**
- Chat responde com dados extraídos de PDFs e CSVs
- Citações incluem título do documento e snippet relevante
- Tabelas são renderizadas na resposta quando pertinente
- **Health check detecta e registra problemas de dados**
- **Admin consegue auditar e gerenciar inconsistencias**

---

## Dependências entre Etapas

```
Etapa 0 (bootstrap / deploy do zero)
    │
    │   Pode ser feita em paralelo com as etapas 1-7,
    │   mas a versão final do entrypoint depende da Etapa 6 (CLI ingester).
    │
    ├──→ Containerfile + docker-compose.yml (independente)
    ├──→ Lifespan validation no app.py (independente)
    └──→ Manifests OpenShift (independente)

Etapa 1 (migration + ports + tabela inconsistencias)
    │
    ├──→ Etapa 2 (download gateway + process gateway)
    │         │
    ├──→ Etapa 3 (repo extensions + inconsistency repo)
    │         │
    │         ▼
    └──→ Etapa 4 (ingestion service) ← depende de 1, 2, 3
              │
              ├──→ Etapa 5 (health check service) ← depende de 2, 3
              │
              ├──→ Etapa 6 (CLI) ← depende de 4, 5
              │         │
              │         └──→ Etapa 0 (versão final do entrypoint com ingester)
              │
              └──→ Etapa 7 (API endpoints) ← depende de 4, 5

Etapa 8 (normalização) ← independente, pode ser feita em paralelo

Etapa 9 (E2E) ← depende de 4, 5, 6 ou 7, e 0
```

**Paralelismo possível:**
- Etapa 0 (parcial: Containerfile, docker-compose, OpenShift) pode começar imediatamente
- Etapas 2 e 3 podem ser feitas em paralelo (ambas dependem apenas da 1)
- Etapa 8 pode ser feita a qualquer momento
- Etapas 6 e 7 podem ser feitas em paralelo (ambas dependem de 4 e 5)
- A versão final da Etapa 0 (entrypoint com ingester) depende da Etapa 6

---

## Estimativa de Volume

| Métrica | Valor estimado |
|---|---|
| Documentos a processar | 122 (91 PDFs + 31 CSVs) |
| Chunks estimados (500 tokens, overlap 50) | ~1.500–3.000 |
| Tabelas estimadas | ~80–150 |
| Tamanho do download total | ~50–200 MB |
| Tempo de processamento (concorrência 3) | ~5–15 minutos |
| URLs a verificar no health check | ~2.259 (890 páginas + 122 docs + ~1.247 links) |
| Tempo do health check (HEAD, concorrência 10) | ~5–10 minutos |

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

### Implicações e melhorias V2

1. **Dados do crawler são sempre sobrescritos** — não há comparação de `last_modified`. Cada execução do `--update` re-extrai e regrava todas as páginas do sitemap, mesmo que não tenham mudado.

2. **Documentos já processados (chunks/tabelas) não são afetados** — o upsert atua na tabela `documents` (metadados), não em `document_contents`/`document_chunks`/`document_tables`. Porém, se o crawler detectar um documento novo na mesma página, ele será inserido com `processing_status = 'pending'` e processado na próxima execução do ingester.

3. **Links obsoletos acumulam** — `page_links` usa `DO NOTHING`, então links que foram removidos do portal continuam no banco. **[V2]** O health check detectará esses links como `broken_link` e os registrará em `data_inconsistencies`.

### Melhorias recomendadas (pós-pipeline)

1. **Atualização condicional por `last_modified`:**
   - O sitemap do Plone já fornece `<lastmod>` em cada URL
   - Comparar com `pages.extracted_at` antes de re-extrair
   - Benefício: reduz carga no portal e no banco em ~90% nas atualizações

2. **Limpeza de links obsoletos:**
   - Antes do upsert de uma página, deletar seus `page_links` e reinserir
   - Ou: marcar links com `crawled_at` e limpar os antigos periodicamente
   - **[V2]** Enquanto isso, o health check identifica links quebrados para o admin

3. **Re-ingestão de documentos alterados:**
   - Se o crawler detectar que `documents.document_url` já existe mas o arquivo no portal mudou (via `Content-Length` ou `ETag`), resetar `processing_status` para `'pending'`
   - **[V2]** O health check detecta mudanças de Content-Length e registra como inconsistencia

4. **Proteção contra sobrescrita manual:**
   - Adicionar coluna `manually_edited` (boolean) em `pages`
   - O upsert ignora páginas com `manually_edited = true`

---

## Riscos e Mitigações

| Risco | Mitigação |
|---|---|
| PDFs são imagens (scanned) sem texto extraível | Log warning, marcar como `error`, **registrar inconsistencia `document_corrupted`** |
| CSVs com encoding diferente (latin-1, windows-1252) | Tentar UTF-8, fallback para latin-1, fallback para error + **inconsistencia `encoding_error`** |
| Documentos muito grandes (> 50MB) | Limite configurável, skip com log + **inconsistencia `oversized`** |
| Portal TRE-PI indisponível durante ingestão | Retry com backoff, status 'error' preserva progresso + **inconsistencia `document_not_found`** |
| Chunks muito longos para contexto do LLM | TextChunker já controla (500 tokens padrão) |
| Tabelas com formatação inconsistente | CsvProcessor já trata; PDFs terão best-effort |
| Duplicatas entre page_content e document_content | `search_chunks()` retorna resultados distintos, LLM deduplicará no prompt |
| Crawler sobrescreve dados a cada execução | Sem impacto nos chunks/tabelas; considerar filtro por `last_modified` (melhoria futura) |
| Links obsoletos acumulam no banco | **[V2] Health check detecta e registra como `broken_link`** |
| Chunks parciais após falha de processamento | **[V2] `save_content_atomic()` com transação e rollback** |
| Endpoints admin expostos sem proteção | **[V2] Autenticação via `X-Admin-Key` header** |
| Health check sobrecarrega o portal | **[V2] Rate limiting (200ms entre requests) + HEAD requests** |
| Inconsistencias duplicadas após múltiplos checks | **[V2] Upsert por `resource_url + inconsistency_type` com incremento de `retry_count`** |

---

## Checklist de Validação Final

### Bootstrap (Etapa 0)
- [ ] `docker-compose up` em ambiente limpo sobe sistema funcional com dados
- [ ] Entrypoint aguarda PostgreSQL antes de continuar (timeout 60s)
- [ ] `alembic upgrade head` é executado automaticamente
- [ ] Crawler roda se banco estiver vazio; é ignorado se já tem dados
- [ ] Crawler `--update` não destrói chunks/tabelas já processados
- [ ] Lifespan do FastAPI rejeita startup se schema não existe
- [ ] Variáveis de ambiente usam prefixo `CRISTAL_` consistente
- [ ] Manifests OpenShift deployam corretamente com `oc apply -f openshift/`
- [ ] InitContainer executa migrations antes do app subir
- [ ] CronJobs de crawler, ingester e **health check** estão configurados

### Pipeline de Ingestão (Etapas 1-4, 6-7)
- [ ] `alembic upgrade head` aplica migration 002 sem erro
- [ ] Tabela `data_inconsistencies` criada com índices
- [ ] `python -m app.adapters.inbound.cli.document_ingester --status` mostra 122 pendentes
- [ ] `python -m app.adapters.inbound.cli.document_ingester --run` processa documentos
- [ ] `document_contents` tem registros com `processing_status = 'done'`
- [ ] `document_chunks` tem chunks com `search_vector` populado
- [ ] `document_tables` tem tabelas extraídas dos CSVs
- [ ] **Erros de ingestão geram registros em `data_inconsistencies`**
- [ ] **`save_content_atomic` garante rollback de chunks parciais**
- [ ] `GET /api/admin/ingestion/status` retorna contagens corretas
- [ ] **Endpoints admin exigem `X-Admin-Key`**

### Health Check e Inconsistencias (Etapa 5) — **[NOVO V2]**
- [ ] `--check` verifica páginas, documentos e links
- [ ] Problemas detectados geram registros em `data_inconsistencies`
- [ ] Recursos que voltam a funcionar são auto-resolvidos
- [ ] `--inconsistencies` lista problemas com filtros
- [ ] `GET /api/admin/ingestion/inconsistencies` retorna lista
- [ ] `GET /api/admin/ingestion/inconsistencies/summary` retorna resumo
- [ ] Admin pode resolver/acknowledge/ignorar inconsistencias via API
- [ ] Upsert não duplica inconsistencias do mesmo recurso+tipo
- [ ] Rate limiting protege o portal TRE-PI durante checks

### Qualidade de Dados (Etapa 8)
- [ ] Categorias normalizadas de 37 para ~18
- [ ] Páginas sem conteúdo removidas/inativadas
- [ ] **Categorias não mapeadas registradas como inconsistencias**

### Validação End-to-End (Etapa 9)
- [ ] Chat responde "Quais estagiários do TRE-PI?" com dados da tabela
- [ ] Chat responde "O que diz a resolução X?" com trechos do PDF
- [ ] Citações incluem documento fonte e snippet
- [ ] **Health check detecta e registra problemas**
- [ ] **Admin pode gerenciar inconsistencias via dashboard/API**
- [ ] Todos os testes passam: `pytest`
