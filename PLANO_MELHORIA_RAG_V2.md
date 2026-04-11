# Plano V2: Busca Híbrida (FTS + Embeddings) + Apresentação Rica

## Contexto

O stemmer português do PostgreSQL trata "diárias" (com acento) e "DIARIAS" (sem acento, como nos CSVs) como stems diferentes (`diár` vs `diari`), causando falhas na busca full-text. Além disso, a busca puramente keyword-based perde contexto semântico (ex: "quanto gastou com viagens" não encontra chunks sobre "diárias pagas").

**Objetivo:** Implementar busca híbrida robusta (FTS com unaccent + embeddings semânticos via Vertex AI) com orquestração inteligente de queries e apresentação rica dos resultados.

**Mudanças em relação ao V1:**
- Tabela separada para embeddings (com versionamento de modelo)
- Embeddings focados em chunks (pages usam summary/description)
- Query expansion condicional (não em toda query)
- Re-ranking LLM excluído da V1
- Fase 4 pode rodar em paralelo com fases 2-3
- Filtragem por metadados na busca semântica
- Cache + circuit breaker para Vertex AI
- ~~Dataset de avaliação para medir melhoria real~~ (removido — sem testes)

**Nota:** Este plano NÃO inclui testes automatizados. A validação será feita manualmente via queries SQL e uso do sistema.

---

## ~~Fase 1: Fix FTS com `unaccent` (PostgreSQL)~~ ✅ CONCLUÍDA

> Implementada no commit `d83673d`.

---

---

## Fase 2: Busca Semântica com Embeddings (Vertex AI + pgvector)

### ~~2a. Infraestrutura~~ ✅ CONCLUÍDA

> Implementada no commit `67bb001`. Docker pgvector + migration 004 tabela embeddings.

---

### ~~2b. Port + Adapter para Embeddings~~ ✅ CONCLUÍDA

- **Port (NOVO)**: `app/domain/ports/outbound/embedding_gateway.py`
  ```python
  class EmbeddingGateway(ABC):
      async def embed_text(self, text: str) -> list[float]: ...
      async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...
  ```
- **Adapter (NOVO)**: `app/adapters/outbound/vertex_ai/embedding_gateway.py`
  - Usa modelo `text-embedding-005` (768 dimensões) via Vertex AI SDK
  - Task type: `RETRIEVAL_DOCUMENT` para indexação, `RETRIEVAL_QUERY` para busca
  - Batch de até 100 textos por chamada (margem segura vs. quota Vertex AI)
  - **Cache LRU** para embeddings de queries (evita recomputar queries repetidas)
  - **Circuit breaker**: se Vertex AI falhar 3x consecutivas, desabilita busca semântica por 60s e usa apenas FTS
  - **Rate limiting**: respeitar quotas do Vertex AI com backoff exponencial

**Validação manual:** testar embed_text com texto de exemplo via script ou REPL.

**Finalização da Fase 2b:**
1. Salvar os registros
2. Fazer commit e push no GitHub

### ~~2c. Integração na Ingestão~~ ✅ CONCLUÍDA

- `app/domain/services/document_ingestion_service.py` — após salvar chunks, gerar embeddings e persistir na tabela `embeddings`:
  - **Chunks**: embedding do `chunk_text` (fonte principal de busca semântica)
  - **Pages**: embedding do `content_summary` ou `description` (não `main_content` — textos longos perdem especificidade)
  - **Tables**: embedding do `search_text` (melhora busca semântica em dados tabulares, atualmente só ILIKE)
- **Política de erro**: se embedding falhar para um chunk, registrar em `data_inconsistencies` (type=`embedding_failed`) e continuar — o documento fica disponível via FTS, sem embedding
- **Hash de texto**: calcular SHA-256 do texto antes de gerar embedding; na re-ingestão, pular chunks cujo hash não mudou

**Validação manual:** ingerir um documento e verificar na tabela `embeddings` que os registros foram criados.

**Finalização da Fase 2c:**
1. Salvar os registros
2. Fazer commit e push no GitHub

### ~~2d. Busca Semântica no SearchRepository~~ ✅ CONCLUÍDA

Novos métodos no port `SearchRepository`:
```python
async def search_semantic(
    self,
    query_embedding: list[float],
    source_type: str = 'chunk',  -- 'chunk' | 'page' | 'table'
    top_k: int = 5,
    filters: dict | None = None  -- {'category': 'licitações', 'content_type': 'pdf'}
) -> list[SemanticMatch]: ...
```

Query SQL com filtragem por metadados:
```sql
SELECT e.*, dc.chunk_text, dc.section_title, dc.page_number,
       dcon.document_title, dcon.document_url,
       1 - (e.embedding <=> $1::vector) AS similarity
FROM embeddings e
JOIN document_chunks dc ON dc.id = e.source_id AND e.source_type = 'chunk'
JOIN document_contents dcon ON dcon.document_url = dc.document_url
WHERE e.source_type = 'chunk'
  AND e.model_name = $2
  AND ($3::text IS NULL OR dcon.document_type = $3)  -- filtro opcional
ORDER BY e.embedding <=> $1::vector
LIMIT $4
```

**Dependência**: `requirements.txt` → adicionar `pgvector>=0.3.0`

**Validação manual:** executar query semântica via SQL ou script e verificar resultados ordenados por similaridade.

**Finalização da Fase 2d:**
1. Salvar os registros
2. Fazer commit e push no GitHub

### ~~2e. Backfill de Embeddings Existentes~~ ✅ CONCLUÍDA

- Script: `scripts/backfill_embeddings.py`
  - Busca todos os chunks/pages/tables sem embedding na tabela `embeddings`
  - Processa em batches de 100
  - Calcula hash do texto e persiste junto com o embedding
  - Relatório final: total processado, falhas, tempo

**Finalização da Fase 2e:**
1. Salvar os registros
2. Fazer commit e push no GitHub

---

## ~~Fase 3: Busca Híbrida com Orquestração Inteligente~~ ✅ CONCLUÍDA

### 3a. HybridSearchService (NOVO)

`app/domain/services/hybrid_search_service.py`:

Implementado como **decorator/wrapper** do `SearchRepository` (mantém mesma interface, fallback trivial para FTS puro).

1. **Busca paralela** (3 estratégias):
   - FTS com `cristal_pt` (query original, via `search_pages` + `search_chunks`)
   - Semântica via embedding (cosine similarity, via `search_semantic`)
   - Tabelas via ILIKE + embedding (já existente + semântico)

2. **Reciprocal Rank Fusion (RRF)** para merge:
   ```python
   score_rrf = sum(1 / (k + rank_i) for each strategy)
   ```
   Com k=60 (constante padrão). Deduplica por URL/chunk_id.

3. **Query Expansion condicional** (NÃO em toda query):
   - Primeira busca: FTS + semântica sem expansão
   - Se resultados combinados < 3 matches com score acima do threshold:
     - Opção A: expandir via dicionário local de sinônimos (zero latência)
     - Opção B: chamar Gemini para gerar 2-3 variações (fallback, +1-2s latência)
   - Ex: "diárias pagas" → ["diárias", "ajuda de custo", "indenização de viagem"]

4. **Re-ranking LLM**: **EXCLUÍDO da V1** — avaliar necessidade após métricas da busca híbrida

5. **Degradação graciosa**:
   - Se Vertex AI indisponível → apenas FTS (sem embedding)
   - Se FTS retornar 0 resultados → tentar query expansion
   - Se tudo falhar → resposta do LLM sem contexto (já funciona hoje)

### 3b. Alterações no ChatService

- `chat_service.py` — injetar `HybridSearchService` como implementação de `SearchRepository`
- O `HybridSearchService` expõe os mesmos métodos (`search_pages`, `search_chunks`, `search_tables`) mas internamente faz merge via RRF
- Pipeline: classify_intent → parallel_search (FTS + semântica) → RRF merge → [expansion condicional] → build_context → LLM → parse

### 3c. Port atualizado

`SearchRepository` ganha método `search_semantic()`. `HybridSearchService` orquestra FTS + semântica e retorna resultados unificados via interface existente.

**Validação manual:**
- Query "quanto gastou com viagens" deve retornar chunks de diárias via similaridade semântica
- Resultados híbridos devem ter recall superior ao FTS puro (verificar manualmente no chat)
- Com Vertex AI desligado, sistema deve funcionar normalmente via FTS

**Finalização da Fase 3:**
1. Salvar os registros
2. Fazer commit e push no GitHub

---

## ~~Fase 4: Apresentação Rica no Frontend~~ ✅ CONCLUÍDA

> Implementada no commit `e83a899`. Metrics cards, markdown headers, tabelas com totalização.

---

---

## ~~Fase 5: Avaliação e Ajuste Fino~~ REMOVIDA

> Fase removida do escopo. Ajustes de parâmetros (k do RRF, top_k, chunk size) serão feitos manualmente conforme necessidade durante uso real do sistema.

---

## Ordem de Implementação

| Ordem | Fase | Descrição | Status |
|-------|------|-----------|--------|
| 1 | 1 | FTS + unaccent (fix bug imediato) | ✅ Concluída |
| 2 | 4 | Apresentação rica no frontend | ✅ Concluída |
| 3 | 2a | Docker pgvector + migration tabela embeddings | ✅ Concluída |
| 4 | 2b | EmbeddingGateway port + adapter + cache/circuit breaker | ✅ Concluída |
| 5a | 2c | Integração na ingestão + EmbeddingRepository | ✅ Concluída |
| 5b | 2d | Busca semântica no SearchRepository | ✅ Concluída |
| 6 | 2e | Backfill embeddings existentes | ✅ Concluída |
| 7 | 3 | HybridSearch + RRF (sem query expansion LLM) | ✅ Concluída |

## Arquivos Críticos

| Arquivo | Ação |
|---------|------|
| `migrations/versions/003_unaccent_fts.py` | NOVO — extension + config + triggers |
| `migrations/versions/004_embeddings.py` | NOVO — pgvector + tabela embeddings separada |
| `app/adapters/outbound/postgres/search_repo.py` | MODIFICAR — cristal_pt + métodos semânticos |
| `app/domain/ports/outbound/embedding_gateway.py` | NOVO — port ABC |
| `app/adapters/outbound/vertex_ai/embedding_gateway.py` | NOVO — adapter Vertex AI + cache + circuit breaker |
| `app/domain/services/hybrid_search_service.py` | NOVO — orquestração + RRF + degradação graciosa |
| `app/domain/services/chat_service.py` | MODIFICAR — usar HybridSearchService |
| `app/domain/services/prompt_builder.py` | MODIFICAR — system prompt aprimorado |
| `app/domain/ports/outbound/search_repository.py` | MODIFICAR — método search_semantic |
| `docker-compose.yml` | MODIFICAR — imagem pgvector |
| `requirements.txt` | MODIFICAR — adicionar pgvector |
| `scripts/backfill_embeddings.py` | NOVO — popular embeddings existentes |
| ~~`scripts/evaluate_search.py`~~ | ~~REMOVIDO — sem testes~~ |
| ~~`tests/evaluation/queries.json`~~ | ~~REMOVIDO — sem testes~~ |
| `static/js/utils.js` | MODIFICAR — markdown/tabelas/metrics |
| `static/js/chat.js` | MODIFICAR — renderizar metrics cards |
| `app/domain/services/document_ingestion_service.py` | MODIFICAR — gerar embeddings + hash |
| `app/config.py` | MODIFICAR — config do embedding model |

## Verificação Final (manual)

1. **FTS com unaccent**: `SELECT plainto_tsquery('cristal_pt', 'diárias')` produz `'diári'` ✅
2. **Busca semântica**: query "quanto gastou com viagens" retorna chunks de diárias via embedding
3. **Híbrida**: resultados combinados são mais relevantes que FTS puro (verificar no chat)
4. **Degradação**: com Vertex AI desligado, sistema funciona normalmente via FTS
5. **Frontend**: tabelas com totalização, metrics cards visíveis, markdown com headers ✅
6. **Performance**: latência da busca híbrida < 3s (sem query expansion LLM)
