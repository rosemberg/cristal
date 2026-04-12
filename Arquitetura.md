# Arquitetura do Sistema — Cristal 2.0

## Descrição do Sistema

**Cristal** é o "Transparência Chat" do TRE-PI (Tribunal Regional Eleitoral do Piauí) — um chatbot com IA que permite a cidadãos consultarem o portal de transparência em linguagem natural. O sistema indexa as páginas do portal, extrai o conteúdo de PDFs e CSVs, e utiliza o Gemini 2.5 Flash Lite (via Vertex AI) para responder perguntas com base nos documentos reais do TRE-PI.

- **LLM:** Gemini 2.5 Flash Lite via Vertex AI
- **Backend:** Python 3.12 + FastAPI (async/await)
- **Frontend:** HTML5 / CSS3 / JavaScript puro (sem frameworks CSS)
- **Banco de dados:** PostgreSQL 16 (via asyncpg + SQLAlchemy)
- **Deploy:** OpenShift 4.x (on-premise), imagem OCI com `uvicorn`

---

## Arquitetura de Alto Nível

O sistema segue arquitetura hexagonal (ports & adapters), com separação clara entre domínio, adaptadores inbound e outbound.

```
╔══════════════════════════════════════════════════════════════════════════╗
║                           CAMADA DE ENTRADA                              ║
╠══════════════════════╦═══════════════════════════╦════════════════════════╣
║   Browser / Cidadão  ║   Admin / Operações       ║  OpenShift CronJobs   ║
║                      ║                            ║                       ║
║  GET  /              ║  GET  /api/admin/metrics  ║  crawler --update     ║
║  POST /api/chat      ║  GET  /api/admin/          ║  (dom. 3h)            ║
║  GET  /api/suggest   ║       inconsistencies      ║                       ║
║  GET  /api/health    ║  PUT  /api/admin/          ║  ingester --run       ║
║                      ║       inconsistencies/{id} ║  (dom. 3h30)          ║
║                      ║  (API Key obrigatória)     ║                       ║
║                      ║                            ║  healthcheck --check  ║
║                      ║                            ║  (dom. 4h)            ║
╚══════════════════════╩═══════════════════════════╩════════════════════════╝
                   │                    │                    │
                   ▼                    ▼                    ▼
╔══════════════════════════════════════════════════════════════════════════╗
║                      ADAPTADORES INBOUND (FastAPI / CLI)                 ║
╠═══════════════════════════╦══════════════════════════════════════════════╣
║  chat_router.py           ║  analytics_router.py  (prefix /api/admin)   ║
║  app.py (lifespan)        ║  document_ingester.py (CLI)                 ║
║                           ║  crawler.py           (CLI)                 ║
╚═══════════════════════════╩══════════════════════════════════════════════╝
                   │
                   ▼
╔══════════════════════════════════════════════════════════════════════════╗
║                          DOMÍNIO (serviços + ports)                      ║
╠══════════════════════════════════════╦═══════════════════════════════════╣
║  PORTS INBOUND (ABCs)                ║  SERVIÇOS                        ║
║                                      ║                                   ║
║  ChatUseCase                         ║  ChatService                     ║
║  DocumentIngestionUseCase            ║  DocumentIngestionService        ║
║  DataHealthCheckUseCase              ║  DataHealthCheckService          ║
╠══════════════════════════════════════╩═══════════════════════════════════╣
║  PORTS OUTBOUND (ABCs)                                                   ║
║                                                                          ║
║  DocumentRepository  · SearchRepository  · PageRepository               ║
║  DocumentProcessGateway  · InconsistencyRepository                      ║
║  VertexAIGateway  · HttpClientPort                                       ║
╚══════════════════════════════════════════════════════════════════════════╝
                   │
                   ▼
╔══════════════════════════════════════════════════════════════════════════╗
║                     ADAPTADORES OUTBOUND (infraestrutura)                ║
╠════════════════╦═══════════════════╦══════════════════╦══════════════════╣
║  PostgreSQL    ║  DocumentProcessor║  VertexAI        ║  HttpClient      ║
║                ║                   ║                  ║                  ║
║  document_     ║  document_        ║  vertex_gateway  ║  download +      ║
║  repo.py       ║  processor.py     ║  .py             ║  HEAD checks     ║
║  search_       ║  pdf_processor.py ║                  ║                  ║
║  repo.py       ║  csv_processor.py ║                  ║                  ║
║  page_repo.py  ║  chunker.py       ║                  ║                  ║
║  inconsistency_║                   ║                  ║                  ║
║  repo.py       ║                   ║                  ║                  ║
╚════════════════╩═══════════════════╩══════════════════╩══════════════════╝
                   │
                   ▼
╔══════════════════════════════════════════════════════════════════════════╗
║                         PostgreSQL — Schema                              ║
╠══════════════════╦═══════════════════╦════════════════════════════════════╣
║  pages           ║  documents        ║  data_inconsistencies            ║
║  page_links      ║  document_        ║                                   ║
║  page_contents   ║    contents       ║  + processing_status             ║
║  crawl_sessions  ║  document_chunks  ║  + processing_error              ║
║  daily_metrics   ║  document_tables  ║  + processed_at                  ║
╚══════════════════╩═══════════════════╩════════════════════════════════════╝
```

---

## Estrutura de Diretórios

```
app/
├── adapters/
│   ├── inbound/
│   │   ├── fastapi/
│   │   │   ├── app.py              # FastAPI app, lifespan, CORS, static files
│   │   │   ├── chat_router.py      # POST /api/chat, GET /api/suggest, GET /api/health
│   │   │   └── analytics_router.py # GET/PUT /api/admin/* (API Key)
│   │   └── cli/
│   │       ├── crawler.py          # Crawler do portal TRE-PI
│   │       └── document_ingester.py# Pipeline de ingestão de PDFs/CSVs
│   └── outbound/
│       ├── postgres/
│       │   ├── document_repo.py
│       │   ├── search_repo.py
│       │   ├── page_repo.py
│       │   └── inconsistency_repo.py
│       ├── document_processor/
│       │   ├── document_processor.py
│       │   ├── pdf_processor.py
│       │   ├── csv_processor.py
│       │   └── chunker.py
│       └── vertex_gateway.py
├── domain/
│   ├── ports/
│   │   ├── inbound/
│   │   │   ├── chat_use_case.py
│   │   │   ├── document_ingestion_use_case.py
│   │   │   └── data_health_check_use_case.py
│   │   └── outbound/
│   │       ├── document_repository.py
│   │       ├── search_repository.py
│   │       ├── page_repository.py
│   │       ├── inconsistency_repository.py
│   │       ├── document_process_gateway.py
│   │       └── vertex_ai_gateway.py
│   ├── services/
│   │   ├── chat_service.py
│   │   ├── document_ingestion_service.py
│   │   └── data_health_check_service.py
│   └── value_objects/
│       ├── chat_message.py
│       └── ingestion.py
migrations/
scripts/
│   └── docker-entrypoint.sh       # Bootstrap: PG → migrations → crawler → ingester → uvicorn
static/                            # Frontend: index.html, css/, js/
openshift/                         # Manifests: Deployment, ConfigMap, Secret, CronJobs
```

---

## Fluxo de uma Pergunta do Cidadão

```
Usuário → POST /api/chat
  → ChatService
    → SearchRepository.search_chunks()   ← trechos extraídos de PDFs/CSVs
    → SearchRepository.search_tables()   ← tabelas de CSVs/PDFs
    → VertexAI / Gemini (prompt com contexto documental)
    → Resposta estruturada: { text, links, extracted_content, suggestions }
  → Frontend renderiza: cards, acordeões, chips de link, botões de sugestão
```

## Fluxo de Bootstrap (deploy do zero)

```
docker-compose up  /  oc apply -f openshift/
  │
  ├─ [1] Aguarda PostgreSQL (timeout 60s)
  ├─ [2] alembic upgrade head  →  cria todas as tabelas
  ├─ [3] Banco vazio? → crawler --full  →  ~890 páginas + 122 docs catalogados
  ├─ [4] document_ingester --run  →  processa PDFs/CSVs → chunks + tabelas
  └─ [5] uvicorn app.main:app  →  sistema 100% funcional
```

## Atualização Periódica (OpenShift CronJobs)

| Job | Horário | Ação |
|---|---|---|
| `cristal-crawler` | Domingos 3h | Recrawla o portal, detecta novas páginas e documentos |
| `cristal-ingester` | Domingos 3h30 | Processa documentos `pending` ou com erro |
| `cristal-healthcheck` | Domingos 4h | Verifica acessibilidade de URLs, registra inconsistências |

---

## Principais Funcionalidades

### Para o Cidadão

| Funcionalidade | Descrição |
|---|---|
| **Chat em linguagem natural** | Pergunta em português, recebe resposta com base nos documentos reais do TRE-PI |
| **RAG documental** | Respostas fundamentadas em trechos extraídos de PDFs e CSVs (licitações, contratos, folha de pagamento, etc.) |
| **Links para fonte** | Cada resposta inclui links diretos para as páginas e documentos consultados |
| **Conteúdo extraído** | Trechos relevantes exibidos inline, sem necessidade de abrir o PDF |
| **Sugestões de continuação** | Botões com perguntas relacionadas para aprofundar a consulta |

### Para Operação / Admin

| Funcionalidade | Descrição |
|---|---|
| **Dashboard de métricas** | Consultas diárias, tempo médio de resposta, taxa de sucesso (`/api/admin/metrics`) |
| **Painel de inconsistências** | Lista centralizada de links quebrados, páginas inacessíveis, documentos corrompidos |
| **Gestão de inconsistências** | Resolver, reconhecer ou ignorar cada problema registrado |
| **Health check de dados** | Verificação periódica de acessibilidade de todas as URLs cadastradas |
| **Pipeline de ingestão** | CLI para processar documentos pendentes, reprocessar erros, ver status |
| **Autenticação de admin** | Todos os endpoints `/api/admin` protegidos por API Key |

### Para Operação / Deploy

| Funcionalidade | Descrição |
|---|---|
| **Bootstrap automático** | `docker-compose up` em ambiente limpo resulta em sistema funcional com dados |
| **Migrations automáticas** | `alembic upgrade head` no entrypoint; InitContainer no OpenShift |
| **Fail-fast sem schema** | App recusa subir se as tabelas não existem (evita crash silencioso) |
| **Manifests OpenShift** | `Deployment`, `Service`, `Route`, `ConfigMap`, `Secret`, `CronJob` prontos |
| **Atualização semanal** | CronJobs mantêm dados sincronizados com o portal do TRE-PI |

---

## Decisões de Design

| Decisão | Justificativa |
|---|---|
| Arquitetura hexagonal | Isolamento do domínio; adaptadores trocáveis sem alterar regras de negócio |
| PostgreSQL (não SQLite) | Suporte a concorrência, full-text search nativo, confiabilidade em produção |
| asyncpg + async/await | Performance em I/O-bound (queries, downloads, chamadas à API do Gemini) |
| Transação atômica em chunks | Evita chunks órfãos se o processamento falhar no meio |
| Tabela `data_inconsistencies` | Rastreamento centralizado de problemas de dados; auditoria pelo admin |
| `docker-entrypoint.sh` | Orquestra bootstrap sem dependência de ferramenta externa (Kubernetes init apenas) |
| API Key para admin | Proteção simples e suficiente para endpoints internos do TRE-PI |
| Frontend vanilla | Sem dependência de frameworks externos; facilita manutenção pela equipe do TRE-PI |

---

## Variáveis de Ambiente Críticas

| Variável | Descrição |
|---|---|
| `CRISTAL_DATABASE_URL` | DSN do PostgreSQL (`postgresql+asyncpg://...`) |
| `CRISTAL_VERTEX_PROJECT_ID` | ID do projeto GCP com Vertex AI habilitado |
| `CRISTAL_VERTEX_LOCATION` | Região do Vertex AI (ex: `us-central1`) |
| `CRISTAL_VERTEX_MODEL` | Modelo Gemini (ex: `gemini-2.5-flash-lite`) |
| `CRISTAL_ADMIN_API_KEY` | Chave para autenticação nos endpoints `/api/admin` |
| `GOOGLE_APPLICATION_CREDENTIALS` | Caminho para o JSON da service account GCP |
| `CRISTAL_ALLOWED_ORIGINS` | Origens CORS permitidas (`*` em dev, domínio TRE-PI em prod) |

---

## Extração de Conteúdo de Documentos

### Fluxo Completo

```
DocumentIngestionService
        │
        │  1. Consulta documents WHERE processing_status = 'pending'
        │  2. Download do arquivo (HttpClient)
        │
        ▼
DocumentProcessor.process(url, bytes, doc_type)
        │
        ├── doc_type = "pdf"  ──→  PdfProcessor
        │                               │
        │                               ├─ fitz.open() (PyMuPDF)
        │                               ├─ page.get_text()  →  texto por página
        │                               ├─ TextChunker.chunk()  →  chunks por página
        │                               └─ page.find_tables()  →  tabelas detectadas
        │
        └── doc_type = "csv"/"xlsx"  ──→  CsvProcessor
                                            │
                                            ├─ pandas.read_csv() / read_excel()
                                            ├─ Extrai todas as abas (XLSX multi-sheet)
                                            ├─ Monta representação texto: "col1 | col2 | ..."
                                            │  (limitado a 100 linhas por aba)
                                            └─ TextChunker.chunk()  →  chunks do texto

        ▼
ProcessedDocument { text, chunks[], tables[], num_pages, title }
        │
        ▼ (transação atômica — rollback total se falhar)
DocumentRepository.save_content()
        │
        ├─ document_contents  ←  texto completo extraído
        ├─ document_chunks    ←  segmentos para RAG (500 tokens, overlap 50)
        └─ document_tables    ←  tabelas estruturadas (headers + rows)
```

### Detalhes por Tipo de Arquivo

**PDF (PyMuPDF / fitz)**

| O que extrai | Como |
|---|---|
| Texto | `page.get_text()` — página por página |
| Tabelas | `page.find_tables()` — best-effort (nem todo PDF tem tabelas detectáveis) |
| Metadados | `doc.metadata["title"]` — título do documento, se existir |
| Chunks | Por página, com índice global contínuo entre páginas |

**CSV / XLSX (pandas)**

| O que extrai | Como |
|---|---|
| Tabelas | Uma `DocumentTable` por aba |
| Texto | `"header1 | header2 | val1 | val2..."` — máximo 100 linhas por aba |
| Chunks | Do texto gerado acima (toda a planilha vira texto primeiro) |

### Chunker (TextChunker)

| Parâmetro | Valor |
|---|---|
| Tamanho alvo | 500 tokens (~385 palavras em português) |
| Overlap | 50 tokens (~38 palavras) |
| Estimativa de tokens | `palavras × 1,3` (heurística para português) |

O overlap garante que o contexto não seja cortado abruptamente entre chunks consecutivos. Os chunks são consumidos pelo `SearchRepository.search_chunks()` na etapa de RAG.

### O que é Persistido no Banco

| Tabela | Conteúdo |
|---|---|
| `document_contents` | Texto completo extraído (PDF inteiro ou CSV completo) |
| `document_chunks` | Segmentos de ~500 tokens com `chunk_index`, `page_number`, `section_title` |
| `document_tables` | Tabelas com `headers[]`, `rows[][]`, `caption`, `page_number` |

Se qualquer parte da persistência falhar, a transação faz rollback completo — nenhum chunk parcial fica no banco.

---

## RAG (Retrieval-Augmented Generation)

O sistema usa **RAG léxico** (keyword-based), não semântico por embeddings. A busca é feita pelo mecanismo full-text nativo do PostgreSQL com dicionário `portuguese`.

### Fluxo Completo

```
Pergunta do usuário: "Qual o valor dos contratos de TI em 2024?"
        │
        ▼
① classify_intent()  →  DATA_QUERY
   (palavras-chave: "valor", "contratos")
        │
        ▼
② Busca em paralelo nas 3 fontes:
   │
   ├─ search_pages(query, top_k=5)
   │    plainto_tsquery('portuguese', $query)
   │    contra search_vector (índice GIN) em pages
   │    retorna: PageMatch { page, score (ts_rank), highlight }
   │
   ├─ search_chunks(query, top_k=5)
   │    plainto_tsquery('portuguese', $query)
   │    contra search_vector (índice GIN) em document_chunks
   │    JOIN document_contents para pegar o título do doc
   │    retorna: ChunkMatch { chunk, document_title, score }
   │
   └─ search_tables(query)
        ILIKE $pattern contra search_text e caption em document_tables
        retorna: DocumentTable[] (máx 10)
        │
        ▼
③ build_context(pages, chunks, tables)
   Monta bloco de texto com 3 seções:

   ## Páginas encontradas
   - [Contratos de TI](https://www.tre-pi.jus.br/...): Descrição da página

   ## Trechos de documentos
   ### Contrato nº 001/2024 - Serviços de TI
   Fonte: https://www.tre-pi.jus.br/.../contrato001.pdf
   <texto do chunk — ~500 tokens>

   ## Tabelas encontradas
   ### Relação de Contratos 2024
   Nº | Objeto | Valor | Vigência
   ---|--------|-------|--------
   001 | Serviços de TI | R$ 450.000 | 12 meses
        │
        ▼
④ Prompt para o Gemini:
   [system_prompt]  →  instruções + formato JSON obrigatório
   [histórico]      →  mensagens anteriores da sessão (se houver)
   [user message]   →  {context}\n\n{pergunta do usuário}
        │
        ▼
⑤ Resposta do Gemini (JSON):
   {
     "text": "Em 2024, o TRE-PI firmou contratos de TI no valor de R$ 450.000...",
     "sources": [{ "document_title": "...", "document_url": "...", "snippet": "...", "page_number": 3 }],
     "tables": [{ "headers": [...], "rows": [...], "source_document": "..." }],
     "suggestions": ["Quais fornecedores de TI?", "Ver contratos de 2023"]
   }
        │
        ▼
⑥ _parse_llm_response()
   JSON direto → tenta json.loads()
   Falhou? → regex extrai de ```json ... ``` ou { ... }
   Falhou tudo? → retorna texto puro (sem sources/tables)
        │
        ▼
⑦ ChatMessage { content, sources[], tables[], suggestions[] }
   → persiste na sessão (se session_id fornecido)
   → log de analytics (intent, pages_found, chunks_found, tables_found, response_time_ms)
   → retorna para o frontend
```

### Tipo de Busca por Fonte

| Fonte | Técnica | Motor |
|---|---|---|
| `pages` | Full-text — `plainto_tsquery` + `ts_rank` + `ts_headline` | PostgreSQL GIN |
| `document_chunks` | Full-text — `plainto_tsquery` + `ts_rank` | PostgreSQL GIN |
| `document_tables` | Substring — `ILIKE %termo%` em `search_text` e `caption` | PostgreSQL sequencial |

### Classificação de Intenção (Intent)

Antes de buscar, o `PromptBuilder.classify_intent()` classifica a pergunta por palavras-chave:

| Intent | Palavras-chave exemplo | Efeito |
|---|---|---|
| `DATA_QUERY` | valor, total, quantos, tabela, servidores | Prioriza chunks e tabelas |
| `DOCUMENT_QUERY` | pdf, documento, relatório, planilha | Prioriza chunks de documentos |
| `NAVIGATION` | onde, como acesso, link, portal | Prioriza páginas |
| `FOLLOWUP` | isso, anterior, continue, mencionou | Usa histórico da sessão |
| `GENERAL_SEARCH` | (demais casos) | Busca balanceada |

### Limitações do RAG Atual

| Limitação | Impacto |
|---|---|
| **Léxico, não semântico** | Não encontra sinônimos ("servidor" ≠ "funcionário") nem conceitos relacionados sem a palavra exata |
| **Sem reranking** | O `ts_rank` do PostgreSQL é um score léxico simples, não considera relevância semântica |
| **Tabelas por ILIKE** | Sem índice de busca — em grandes volumes pode ser lento |
| **top_k fixo em 5** | Contexto limitado a 5 páginas + 5 chunks + 10 tabelas independente da pergunta |

Para evoluir para RAG semântico, o próximo passo seria adicionar embeddings (ex: `pgvector` + modelo de embeddings em português) e substituir `plainto_tsquery` por busca por similaridade de vetores.
