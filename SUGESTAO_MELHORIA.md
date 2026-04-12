# Sugestões de Melhoria — Sistema de RAG

## Contexto

O RAG atual do Cristal usa busca **léxica** (keyword-based) com PostgreSQL full-text search e dicionário `portuguese`. As sugestões abaixo estão organizadas em três níveis de complexidade crescente.

---

## Nível 1 — Melhorias Imediatas (sem mudar a stack)

### 1.1 Busca híbrida em tabelas
Trocar o `ILIKE` por `tsvector` na tabela `document_tables`, adicionando um índice GIN em `search_text`. Hoje a busca em tabelas é sequencial — sem índice.

### 1.2 Reranking por múltiplos sinais
Combinar `ts_rank` com outros fatores antes de enviar ao Gemini:
- Data de atualização do documento (mais recente = mais relevante)
- Número de palavras da query que aparecem no chunk
- Presença do chunk na página mais relevante

### 1.3 Expansão de query
Antes de buscar, expandir a query com sinônimos conhecidos do domínio:
```
"servidor" → "servidor público | funcionário | cargo"
"licitação" → "licitação | pregão | concorrência | dispensa"
```
Resolve o principal problema do RAG léxico sem adicionar complexidade de infraestrutura.

### 1.4 Ajuste dinâmico do top_k por intent
Hoje `top_k=5` é fixo para todas as buscas. Poderia variar por intent:
- `DATA_QUERY` → mais chunks, menos páginas
- `NAVIGATION` → mais páginas, sem chunks
- `DOCUMENT_QUERY` → só chunks + tabelas

---

## Nível 2 — RAG Semântico com pgvector

A maior evolução: substituir (ou complementar) a busca léxica por busca por similaridade de vetores.

### O que muda na arquitetura

```
Hoje:
  pergunta → plainto_tsquery → ts_rank → top 5 chunks

Com pgvector:
  pergunta → modelo de embedding → vetor [768 dims]
           → SELECT ... ORDER BY embedding <=> $query_vec LIMIT 5
```

### Passos necessários

1. Adicionar `pgvector` ao PostgreSQL — extensão nativa, sem serviço externo
2. Adicionar coluna `embedding VECTOR(768)` em `document_chunks`
3. Gerar embeddings na ingestão — ao salvar cada chunk, chamar um modelo de embedding
4. Busca por cosine similarity — `embedding <=> query_embedding`
5. Busca híbrida — combinar score léxico (`ts_rank`) + score semântico (`1 - cosine_distance`) com peso configurável

### Modelo de embedding recomendado

Para português, o melhor custo-benefício é usar a própria API do Gemini (`text-embedding-004`) via Vertex AI — já há integração no projeto, sem adicionar nova dependência.

---

## Nível 3 — Técnicas Avançadas de RAG

### 3.1 Chunking hierárquico (Parent-Child)
Hoje cada chunk é independente. Com chunking hierárquico:
- Chunks pequenos (~100 tokens) para busca precisa
- Chunks grandes (~500 tokens, o "parent") para contexto enviado ao LLM
- Busca no filho, envia o pai → melhor precisão na recuperação + contexto rico

### 3.2 HyDE (Hypothetical Document Embeddings)
Antes de buscar, pedir ao Gemini para gerar uma resposta hipotética, depois buscar documentos similares a essa resposta hipotética. Melhora para perguntas vagas ou indiretas.

### 3.3 Re-ranker cross-encoder
Após recuperar top-20 candidatos, aplicar um modelo cross-encoder leve para reordenar e selecionar os top-5 mais relevantes. Mais preciso que qualquer score de busca isolado.

### 3.4 Metadata filtering
Antes de buscar full-text/semântico, filtrar por metadados já presentes:
- `category` e `subcategory` da página
- `doc_type` (pdf/csv)
- `detected_at` (range de datas)

Reduz o espaço de busca e aumenta precisão.

---

## Resumo de Impacto vs. Esforço

| Melhoria | Impacto | Esforço | Dependência nova |
|---|---|---|---|
| Índice GIN em tabelas | Médio | Baixo | Nenhuma |
| Expansão de query por domínio | Alto | Baixo | Nenhuma |
| top_k dinâmico por intent | Médio | Baixo | Nenhuma |
| pgvector + embeddings Gemini | Alto | Médio | `pgvector` (extensão PG) |
| Busca híbrida léxico + semântico | Muito alto | Médio | `pgvector` |
| Chunking hierárquico | Alto | Médio | Nenhuma |
| HyDE | Alto | Alto | Latência extra (1 chamada LLM a mais) |
| Re-ranker cross-encoder | Muito alto | Alto | Modelo adicional |

---

## Caminho Recomendado

**Nível 1** (sem custo, ganho imediato) → **pgvector + embeddings Gemini** (máximo impacto com a stack já existente) → **busca híbrida léxico + semântico** como resultado final.
