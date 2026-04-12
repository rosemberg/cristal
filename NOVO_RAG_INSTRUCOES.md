# NOVO_RAG_INSTRUCOES.md — Sistema RAG do Cristal

> **Projeto:** Cristal — Transparência Chat (TRE-PI)
> **Data:** 2026-04-12
> **Versão RAG:** 2.0 (6 fases implementadas)

---

## 1. O que é o Sistema RAG do Cristal

O Cristal é um chatbot de transparência pública do Tribunal Regional Eleitoral do Piauí (TRE-PI). Seu núcleo é um pipeline de **RAG (Retrieval-Augmented Generation)** que permite ao cidadão fazer perguntas em linguagem natural e receber respostas fundamentadas nos documentos e páginas do portal de transparência.

O sistema opera em dois momentos distintos:

- **Offline — Pipeline de ingestão:** coleta, processa, enriquece e indexa o conteúdo do portal.
- **Online — Pipeline de resposta:** recebe a pergunta, busca o conteúdo relevante, e orquestra o LLM para gerar uma resposta precisa.

---

## 2. Componentes do Sistema

### 2.1 Coleta de Dados (Crawler)

O crawler (`app/adapters/inbound/cli/crawler.py`) percorre o portal `tre-pi.jus.br` e armazena:

- **Páginas** (`pages`): URL, título, categoria, subcategoria, `main_content` (HTML da página), `content_summary` (sumário gerado por LLM).
- **Documentos** (`documents`): links para PDFs e CSVs encontrados nas páginas.
- **Links entre páginas** (`page_links`): grafo de hiperlinks coletados durante o crawl.

### 2.2 Processamento de Documentos

O pipeline de ingestão (`app/adapters/inbound/cli/document_ingester.py`) processa cada documento:

- **PDFs** → extraídos com PyMuPDF (`PdfProcessor`) → texto por página.
- **CSVs/XLSXs** → extraídos com pandas (`CsvProcessor`) → tabelas estruturadas.

Ambos os processadores alimentam:
- **`document_contents`**: texto completo do documento.
- **`document_chunks`**: fragmentos de texto para busca semântica.
- **`document_tables`**: tabelas estruturadas para análise por function calling.

### 2.3 Chunking Semântico (Fase 4)

O `SemanticChunker` (`app/adapters/outbound/document_processor/semantic_chunker.py`) divide o conteúdo respeitando a estrutura do documento:

| Elemento | Tratamento |
|----------|------------|
| `h1`–`h6` | Inicia novo chunk; preserva `section_title` |
| `<p>` | Acumula no chunk corrente |
| `<table>` | Chunk isolado com headers em formato pipe |
| `<ul>/<ol>` | Mantido junto se < 300 tokens |
| Texto plano | Divisão por parágrafo duplo (`\n\n`) |

Limites: **mínimo 100 tokens**, **máximo 800 tokens** por chunk. Chunks de versão 2 (`version=2`) substituem os chunks legados (`version=1`).

Cada chunk recebe um **prefixo contextual**:
```
Transparência > Licitações > Pregão 2024

A empresa XYZ venceu o certame...
```

### 2.4 Perguntas Sintéticas — Query Augmentation (Fase 1)

Para cada chunk, o LLM gera 3–5 perguntas em linguagem natural que esse chunk é capaz de responder. As perguntas são armazenadas em `synthetic_queries` e recebem embeddings próprios com `source_type='synthetic_query'`.

**Problema resolvido:** um chunk com tabela de diárias (colunas `NOME`, `VALOR`, `DESTINO`) está semanticamente distante da pergunta _"Quanto o tribunal gastou com diárias em 2024?"_. Com as perguntas sintéticas, esse match passa a ocorrer.

CLI: `python -m app.adapters.inbound.cli.synthetic_queries --generate`

### 2.5 Sumarização e Indexação Multinível (Fase 2)

- Sumário de página (`pages.content_summary`): 2–3 frases geradas por LLM com foco em período, tipo e conteúdo da página.
- Sumário de seção (`document_contents.section_summaries`): para PDFs com > 10 páginas, sumário por grupo de páginas.
- Embeddings com `source_type='page_summary'` e `source_type='section_summary'` participam da busca semântica.

CLI: `python -m app.adapters.inbound.cli.content_summarizer --summarize`

### 2.6 Enriquecimento de Metadados Estruturados (Fase 3)

O `MetadataEnricher` extrai entidades e classifica cada página em duas etapas:

**Etapa A — NER via regex (sem LLM, rápido):**

| Entidade | Exemplo |
|----------|---------|
| `date_range` | `exercício 2024`, `1º semestre 2024` |
| `monetary_value` | `R$ 1.234.567,89` |
| `process_number` | `0001234-12.2024.6.18.0000` |
| `contract_number` | `Contrato nº 045/2023` |
| `pregao` | `Pregão Eletrônico nº 012/2024` |
| `cpf_cnpj` | `12.345.678/0001-90` |

**Etapa B — Classificação temática via LLM (batch):**
Atribui 1–3 tags semânticas por página a partir de 16 categorias normalizadas (ex: `licitacao`, `contrato`, `diaria`, `folha_pagamento`, `orcamento`...).

As entidades ficam em `page_entities`; as tags em `page_tags`.

CLI: `python -m app.adapters.inbound.cli.metadata_enricher --enrich --step all`

### 2.7 Scoring de Qualidade de Chunks (Fase 5)

O `ChunkQualityScorer` atribui um score de 0.0 a 1.0 a cada chunk com base em 6 critérios ponderados:

| Critério | Peso | Flag emitida |
|----------|------|--------------|
| Comprimento adequado (100–800 tokens) | 0.15 | `too_short`, `too_long` |
| Densidade de palavras reais | 0.25 | `low_density` |
| Ausência de artefatos OCR | 0.20 | `ocr_artifacts` |
| Coerência de sentenças | 0.15 | `incoherent` |
| Ausência de duplicação interna | 0.10 | `internal_dup` |
| Presença de conteúdo informativo | 0.15 | `low_information` |

Chunks com score < 0.5 são **quarentenados** (`quarantined=true`) e excluídos da busca. Duplicatas são detectadas por hash SHA-256 do texto normalizado.

CLI: `python -m app.adapters.inbound.cli.quality_check --score --deduplicate --report`

### 2.8 Grafo de Relações entre Documentos (Fase 6)

O `RelationExtractor` cria um grafo leve de relações semânticas entre páginas:

| Estratégia | Fonte | Tipos de relação |
|------------|-------|-----------------|
| `entity` | Páginas com mesmo `entity_value` em `page_entities` | `referencia`, `origina`, `decorre_de` |
| `link` | Links `<a href>` em `main_content` apontando para o portal | `referencia` |

Relações ficam em `document_relations` com campos `confidence` e `strategy`.

CLI: `python -m app.adapters.inbound.cli.extract_relations --extract --strategy all`

### 2.9 Busca Híbrida com RRF

O `HybridSearchService` combina 3 estratégias em paralelo e as funde via **Reciprocal Rank Fusion (RRF, k=60)**:

1. **FTS (Full-Text Search):** `tsvector` com `unaccent` e dicionário português no PostgreSQL.
2. **Busca semântica por chunks:** similaridade coseno nos embeddings de `document_chunks`, `page_chunks` e `synthetic_queries`.
3. **Busca semântica por páginas:** similaridade coseno em `page`, `page_summary` e `section_summary`.

Se o número de resultados for insuficiente (< 3), é ativada a **query expansion** por dicionário local de sinônimos (ex: `"diárias"` → `"ajuda de custo, indenização de viagem"`).

### 2.10 Geração da Resposta (LLM Orchestration)

O pipeline de resposta tem dois modos:

**Modo simples — `ChatService`:**
1. Classifica a intenção da query (`QueryIntent`).
2. Busca páginas e chunks relevantes via `HybridSearchService`.
3. Valida e filtra tabelas com `TableValidatorAgent`.
4. Constrói o prompt com `PromptBuilder` (breadcrumb + chunks + tabelas + histórico).
5. Chama o Gemini 2.5 Flash Lite via Vertex AI.
6. Formata a resposta com citações, links e sugestões.

**Modo multi-agente — `MultiAgentChatService`:**
1. `DataAgent` (function calling): analisa tabelas e computa métricas com ferramentas Python (`sum_column`, `filter_rows`, `count_rows`...).
2. `WriterAgent`: recebe o `AnalysisResult` do DataAgent e redige a resposta final em linguagem natural.
3. `ResponseAssembler`: monta o JSON final com `text`, `citations`, `metrics`, `links` e `suggestions`.

---

## 3. Modelo de Dados (Principais Tabelas)

```
pages                   — Páginas do portal (URL, categoria, main_content, content_summary)
page_chunks             — Chunks semânticos das páginas HTML
page_entities           — Entidades extraídas (NER): datas, valores, contratos, CNPJ
page_tags               — Tags semânticas normalizadas: licitacao, contrato, diaria...
page_links              — Hiperlinks entre páginas (grafo do crawler)

documents               — Documentos PDF/CSV vinculados às páginas
document_contents       — Texto completo + section_summaries dos documentos
document_chunks         — Chunks semânticos dos documentos (version, has_table, quality_score)
document_tables         — Tabelas estruturadas extraídas dos documentos

embeddings              — Vetores gerados pelo gemini-embedding-001
                          (source_type: chunk | page_chunk | synthetic_query |
                                        page | page_summary | section_summary)

synthetic_queries       — Perguntas sintéticas geradas por LLM para cada chunk
document_relations      — Grafo de relações: referencia, origina, complementa, substitui...
data_inconsistencies    — Log de erros e anomalias do pipeline de ingestão
```

---

## 4. Migrations (Alembic)

| # | Arquivo | Conteúdo |
|---|---------|----------|
| 001 | `initial_schema` | Tabelas base: pages, documents, page_links |
| 002 | `document_processing` | document_contents, document_chunks, document_tables, data_inconsistencies |
| 003 | `unaccent_fts` | Extensão unaccent + vetores tsvector para FTS em PT-BR |
| 004 | `embeddings` | Tabela embeddings + índice IVFFlat (pgvector) |
| 005 | `page_chunks` | Chunks semânticos das páginas HTML |
| 006 | `synthetic_queries` | Tabela synthetic_queries + índice por source |
| 007 | `fix_embeddings_source_type` | Correção de enum de source_type |
| 008 | `content_summaries` | Campo content_summary em pages + section_summaries em document_contents |
| 009 | `enriched_metadata` | Tabelas page_entities e page_tags |
| 010 | `semantic_chunks` | Colunas version, has_table, parent_chunk_id em document_chunks e page_chunks |
| 011 | `chunk_quality` | Colunas quality_score, quality_flags, quarantined nos chunks |
| 012 | `document_relations` | Tabela document_relations com índices por entidade e tipo |

Aplicar todas as migrations:
```bash
alembic upgrade head
```

---

## 5. Ordem de Execução dos Pipelines

Execute os pipelines nessa sequência após subir o banco com `alembic upgrade head`:

```bash
# 1. Coleta de dados
python -m app.adapters.inbound.cli.crawler --crawl

# 2. Ingestão de documentos (PDF/CSV)
python -m app.adapters.inbound.cli.document_ingester --ingest

# 3. Rechunk semântico (substitui chunks legados por versão 2)
python -m app.adapters.inbound.cli.rechunk_documents --execute
python -m app.adapters.inbound.cli.rechunk_documents --reembed   # regera embeddings

# 4. Perguntas sintéticas (Query Augmentation)
python -m app.adapters.inbound.cli.synthetic_queries --generate

# 5. Sumarização multinível
python -m app.adapters.inbound.cli.content_summarizer --summarize

# 6. Enriquecimento de metadados
python -m app.adapters.inbound.cli.metadata_enricher --enrich --step all

# 7. Qualidade de chunks
python -m app.adapters.inbound.cli.quality_check --score --target all
python -m app.adapters.inbound.cli.quality_check --deduplicate --target all
python -m app.adapters.inbound.cli.quality_check --report

# 8. Grafo de relações (depende dos metadados da etapa 6)
python -m app.adapters.inbound.cli.extract_relations --extract --strategy all
python -m app.adapters.inbound.cli.extract_relations --status
```

> **Nota:** As etapas 3–8 podem ser re-executadas independentemente. As etapas 4, 5 e 7 devem ser re-executadas sempre que o rechunk (etapa 3) for repetido.

---

## 6. Métricas de Sucesso

| Métrica | Alvo |
|---------|------|
| Recall@5 (resposta correta nos top-5) | > 85% |
| MRR — Mean Reciprocal Rank | > 0.70 |
| Cobertura de entidades (páginas com ≥ 1 entidade) | > 80% |
| Taxa de quarentena (chunks com score < 0.5) | < 15% |
| Chunks por resposta (média) | 3–5 |

---

## 7. Arquitetura em Alto Nível

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                         PIPELINE OFFLINE — INGESTÃO                            ║
╠══════════════════════════════════════════════════════════════════════════════════╣
║                                                                                  ║
║   tre-pi.jus.br                                                                  ║
║       │                                                                          ║
║       ▼                                                                          ║
║  ┌─────────────┐     páginas + links      ┌──────────────────────────────────┐  ║
║  │   Crawler   │ ───────────────────────► │          PostgreSQL               │  ║
║  └─────────────┘                          │                                   │  ║
║                                           │  pages          page_links        │  ║
║  ┌─────────────────────────────────────┐  │  documents      document_tables   │  ║
║  │       Document Ingester             │  │  document_      page_chunks        │  ║
║  │                                     │  │  contents       document_chunks   │  ║
║  │  PdfProcessor ── SemanticChunker    │ ─┤                                   │  ║
║  │  CsvProcessor ── TextChunker        │  │  embeddings     synthetic_queries  │  ║
║  └─────────────────────────────────────┘  │  page_entities  page_tags          │  ║
║                                           │  document_      data_              │  ║
║  ┌──────────────────────┐                 │  relations      inconsistencies    │  ║
║  │  Vertex AI           │                 └──────────────────────────────────┘  ║
║  │  gemini-embedding-001│                           ▲  ▲  ▲  ▲  ▲  ▲           ║
║  └──────────────────────┘                           │  │  │  │  │  │           ║
║           │ embeddings                              │  │  │  │  │  │           ║
║           └─────────────────────────────────────────┘  │  │  │  │  │           ║
║                                                         │  │  │  │  │           ║
║  ┌──────────────────────────────────────────────────────┘  │  │  │  │           ║
║  │  SyntheticQueryGenerator     (Fase 1)                    │  │  │  │           ║
║  │   LLM gera 3-5 perguntas por chunk → embeddings próprios │  │  │  │           ║
║  └──────────────────────────────────────────────────────────┘  │  │  │           ║
║                                                                 │  │  │           ║
║  ┌──────────────────────────────────────────────────────────────┘  │  │           ║
║  │  ContentSummarizer           (Fase 2)                           │  │           ║
║  │   Sumário de página (2-3 frases) + sumário de seção             │  │           ║
║  └─────────────────────────────────────────────────────────────────┘  │           ║
║                                                                        │           ║
║  ┌─────────────────────────────────────────────────────────────────────┘           ║
║  │  MetadataEnricher            (Fase 3)                                           ║
║  │   Etapa A: NER regex → datas, valores, contratos, CNPJ                          ║
║  │   Etapa B: LLM batch → tags semânticas (licitacao, contrato, diaria...)         ║
║  └─────────────────────────────────────────────────────────────────────────────────║
║                                                                                     ║
║  ┌─────────────────────────────────────────────────────────────────────────────────║
║  │  ChunkQualityScorer          (Fase 5)                                           ║
║  │   6 critérios ponderados → quality_score; quarantined=true se score < 0.5       ║
║  │   Deduplicação por SHA-256 do texto normalizado                                 ║
║  └─────────────────────────────────────────────────────────────────────────────────║
║                                                                                     ║
║  ┌─────────────────────────────────────────────────────────────────────────────────║
║  │  RelationExtractor           (Fase 6)                                           ║
║  │   Estratégia entity: liga páginas com mesma entidade (ex: Pregão 012/2024)      ║
║  │   Estratégia link:   links internos do portal → relação "referencia"            ║
║  └─────────────────────────────────────────────────────────────────────────────────║
╚══════════════════════════════════════════════════════════════════════════════════════╝


╔══════════════════════════════════════════════════════════════════════════════════════╗
║                       PIPELINE ONLINE — RESPOSTA AO CIDADÃO                        ║
╠══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                      ║
║   Cidadão                                                                            ║
║      │  "Quanto o TRE-PI gastou com diárias em 2024?"                               ║
║      ▼                                                                               ║
║  ┌──────────────────────────────────────────────────────────────────────────────┐   ║
║  │                         FastAPI  /api/chat                                    │   ║
║  └─────────────────────────────────────┬────────────────────────────────────────┘   ║
║                                        │                                             ║
║                                        ▼                                             ║
║  ┌──────────────────────────────────────────────────────────────────────────────┐   ║
║  │                       ChatService / MultiAgentChatService                     │   ║
║  │                                                                               │   ║
║  │  1. Classifica intenção  (QueryIntent: factual / data / navigation / general) │   ║
║  │  2. Gera embedding da query  ──────────────────────► Vertex AI               │   ║
║  │                                                                               │   ║
║  │  3. HybridSearchService  ◄──────────────────────────── PostgreSQL             │   ║
║  │     ├── FTS (tsvector + unaccent)           pages, chunks                    │   ║
║  │     ├── Semântica chunks  (coseno)          embeddings: chunk, page_chunk,   │   ║
║  │     │                                                   synthetic_query      │   ║
║  │     ├── Semântica páginas (coseno)          embeddings: page, page_summary,  │   ║
║  │     │                                                   section_summary      │   ║
║  │     └── RRF k=60  ──► merge + rerank        (query expansion se < 3 hits)   │   ║
║  │                                                                               │   ║
║  │  4. TableValidatorAgent  — filtra tabelas corrompidas antes do LLM           │   ║
║  │                                                                               │   ║
║  │  ┌──────────────── Modo multi-agente ───────────────────────────────────┐   │   ║
║  │  │  DataAgent (function calling)                                         │   │   ║
║  │  │   ├── sum_column / filter_rows / count_rows / pivot_table            │   │   ║
║  │  │   └── AnalysisResult → { selected_tables, metrics, data_summary }   │   │   ║
║  │  │                                                                       │   │   ║
║  │  │  WriterAgent                                                          │   │   ║
║  │  │   └── Redige resposta em linguagem natural a partir do AnalysisResult │   │   ║
║  │  └───────────────────────────────────────────────────────────────────────┘   │   ║
║  │                                                                               │   ║
║  │  5. PromptBuilder — monta contexto: breadcrumb + chunks + tabelas + histórico│   ║
║  │  6. Gemini 2.5 Flash Lite  ◄───────────────────────────── Vertex AI          │   ║
║  │  7. ResponseAssembler — { text, citations, metrics, links, suggestions }     │   ║
║  └──────────────────────────────────────────────────────────────────────────────┘   ║
║                                        │                                             ║
║                                        ▼                                             ║
║   Cidadão  ◄──  Resposta formatada com citações, links e sugestões de perguntas     ║
╚══════════════════════════════════════════════════════════════════════════════════════╝


╔══════════════════════════════════════════════════════════════════════════════════════╗
║               MODELO DE DADOS — VISÃO GERAL DO GRAFO DE CONHECIMENTO               ║
╠══════════════════════════════════════════════════════════════════════════════════════╣
║                                                                                      ║
║   ┌─────────────────────────────────────────────────────────────────────────────┐   ║
║   │                              pages                                           │   ║
║   │  id · url · category · subcategory · main_content · content_summary         │   ║
║   └───┬──────────────┬─────────────────┬────────────────┬───────────────────────┘   ║
║       │              │                 │                │                            ║
║       ▼              ▼                 ▼                ▼                            ║
║  page_chunks    page_entities      page_tags      page_links                        ║
║  (semântico)    (NER: datas,      (licitacao,    (hiperlinks                        ║
║                  contratos,        contrato,      crawleados)                       ║
║                  CNPJ...)          diaria...)                                        ║
║       │                                                  │                           ║
║       ▼                                                  ▼                           ║
║  embeddings ◄────────────────────────────────── document_relations                  ║
║  (coseno)        pages são ligadas por            (referencia,                      ║
║                  entidade compartilhada            origina,                         ║
║                  ou por link interno)              complementa...)                  ║
║                                                                                      ║
║   ┌─────────────────────────────────────────────────────────────────────────────┐   ║
║   │                            documents                                         │   ║
║   │  id · document_url · page_url · document_type · processing_status           │   ║
║   └───┬──────────────┬───────────────────────────────────────────────────────────┘  ║
║       │              │                                                               ║
║       ▼              ▼                                                               ║
║  document_      document_        document_        synthetic_                        ║
║  contents       chunks           tables           queries                           ║
║  (full_text,    (semântico,      (estruturado,    (3-5 perguntas                    ║
║   summaries)    quality_score,   function         por chunk,                        ║
║                 quarantined)     calling)         embedding próprio)                ║
║                                                                                      ║
╚══════════════════════════════════════════════════════════════════════════════════════╝
```
