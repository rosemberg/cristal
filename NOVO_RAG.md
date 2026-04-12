# NOVO_RAG.md — Plano de Melhoria da Qualidade dos Dados RAG

> **Projeto:** Cristal — Transparência Chat (TRE-PI)
> **Objetivo:** Pós-processamento dos dados já coletados (890 páginas, 122 documentos) para elevar a qualidade da busca e das respostas ao cidadão.
> **Data:** 2026-04-12

---

## Visão Geral

| Ordem | Fase | Esforço | Impacto na RAG | Dependências |
|-------|------|---------|----------------|--------------|
| 1 | Perguntas Sintéticas (Query Augmentation) | Médio | Muito Alto | Embeddings funcionando |
| 2 | Sumarização e Indexação Multinível | Baixo | Alto | Campo `content_summary` já existe |
| 3 | Enriquecimento de Metadados Estruturados | Médio | Alto | Nenhuma |
| 4 | Chunking Semântico | Médio | Alto | Reprocessar documentos |
| 5 | Detecção e Correção de Dados Corrompidos | Baixo | Médio | Nenhuma |
| 6 | Cross-referência e Grafo de Conhecimento Leve | Alto | Médio | NER da Fase 3 |

---

## Fase 1 — Perguntas Sintéticas (Query Augmentation)

### Problema

O embedding de um chunk burocrático (ex: tabela de diárias com colunas `NOME`, `VALOR`, `DESTINO`) está semanticamente distante da pergunta natural do cidadão ("Quanto o tribunal gastou com diárias em 2024?"). Isso reduz o recall da busca semântica.

### Solução

Para cada chunk existente, gerar 3-5 perguntas em linguagem natural que ele é capaz de responder. Essas perguntas recebem embeddings próprios e participam da busca semântica.

### Implementação

#### 1.1 — Modelo de dados

```sql
-- Migration: 006_synthetic_queries.py
CREATE TABLE synthetic_queries (
    id            SERIAL PRIMARY KEY,
    source_type   TEXT NOT NULL,            -- 'page_chunk', 'document_chunk', 'table'
    source_id     INTEGER NOT NULL,         -- FK lógico para o chunk/tabela de origem
    question      TEXT NOT NULL,            -- Pergunta gerada
    created_at    TIMESTAMPTZ DEFAULT now(),
    model_used    TEXT NOT NULL             -- Ex: 'gemini-2.5-flash-lite'
);

CREATE INDEX idx_sq_source ON synthetic_queries(source_type, source_id);
```

Os embeddings das perguntas sintéticas são armazenados na tabela `embeddings` existente com `source_type = 'synthetic_query'` e `source_id` apontando para `synthetic_queries.id`.

#### 1.2 — Serviço de geração (batch)

```
app/domain/services/synthetic_query_generator.py
```

**Responsabilidades:**
- Recebe uma lista de chunks (texto + metadados)
- Envia ao LLM em batches de 10-20 chunks por chamada
- Prompt template:

```
Você é um especialista em transparência pública. Para cada trecho de documento
abaixo, gere de 3 a 5 perguntas que um cidadão faria e que esse trecho responde.

Regras:
- Perguntas em português brasileiro, linguagem simples
- Inclua variações (formal/informal, com/sem siglas)
- Se houver dados numéricos, inclua perguntas sobre valores e totais
- Se houver nomes, inclua perguntas sobre pessoas/órgãos específicos
- Retorne JSON: [{"chunk_id": N, "questions": ["...", "..."]}]

Trechos:
{chunks_json}
```

- Parse do JSON de resposta com fallback para regex
- Retry com backoff exponencial (3 tentativas)

**Interface (porta inbound):**

```python
class SyntheticQueryGenerationUseCase(Protocol):
    async def generate_for_pending_chunks(self, batch_size: int = 50) -> GenerationResult:
        """Gera perguntas para chunks que ainda não têm perguntas sintéticas."""
        ...

    async def regenerate_for_chunk(self, source_type: str, source_id: int) -> int:
        """Regenera perguntas para um chunk específico. Retorna qtd gerada."""
        ...
```

#### 1.3 — Integração com a busca

No `HybridSearchService.search()`:

1. O embedding da query do usuário já é gerado (sem mudança)
2. Adicionar uma 5ª estratégia paralela: `search_synthetic_queries()`
   - Busca por similaridade coseno na tabela `embeddings` onde `source_type = 'synthetic_query'`
   - Para cada match, resolve o `source_id` → `synthetic_queries.source_id` → chunk original
   - Retorna o chunk original (não a pergunta) com o score de similaridade
3. O resultado entra no RRF merge normalmente

#### 1.4 — CLI de execução

```bash
# Gerar para todos os chunks pendentes
python -m app.adapters.inbound.cli.synthetic_queries --generate

# Status
python -m app.adapters.inbound.cli.synthetic_queries --status

# Regenerar para um chunk específico
python -m app.adapters.inbound.cli.synthetic_queries --regenerate --source-type page_chunk --source-id 42
```

#### 1.5 — Estimativa de custo

- ~890 páginas + chunks de documentos → estimativa ~3.000 chunks total
- 3-5 perguntas por chunk → ~12.000 perguntas sintéticas
- ~12.000 embeddings adicionais (gemini-embedding-001)
- Custo LLM: ~1.5M tokens de entrada + ~600K de saída (Gemini Flash Lite)
- Custo embedding: ~3.6M tokens (12K perguntas × ~300 tokens médios)

---

## Fase 2 — Sumarização e Indexação Multinível

### Problema

A busca opera em dois níveis (página inteira via FTS, chunks via embeddings), mas falta um nível intermediário. O LLM recebe chunks brutos sem contexto do documento completo, gerando respostas fragmentadas.

### Solução

Gerar sumários em 2 níveis (documento e seção) e indexá-los separadamente, criando uma hierarquia: sumário → chunk → texto completo.

### Implementação

#### 2.1 — Sumarização de páginas

O campo `pages.content_summary` já existe e está vazio. Preenchê-lo via batch LLM.

```
app/domain/services/content_summarizer.py
```

**Prompt template para páginas:**

```
Resuma o conteúdo abaixo em 2-3 frases objetivas. O contexto é transparência
pública do TRE-PI. Foque em: o que o documento contém, período de referência,
e tipo de informação (financeira, administrativa, licitação, etc.).

Título: {title}
Categoria: {category} > {subcategory}
Conteúdo:
{main_content[:4000]}
```

**Persistência:** `UPDATE pages SET content_summary = $1 WHERE id = $2`

#### 2.2 — Sumarização de documentos longos (por seção)

Para PDFs com >10 páginas, gerar sumário por grupo de páginas (seções lógicas).

```sql
-- Novo campo em document_contents
ALTER TABLE document_contents ADD COLUMN section_summaries JSONB;
-- Formato: [{"section": "Receitas", "pages": "1-5", "summary": "..."}, ...]
```

#### 2.3 — Embeddings dos sumários

- Gerar embedding para cada `content_summary` com `source_type = 'page_summary'`
- Gerar embedding para cada seção de documento com `source_type = 'section_summary'`

#### 2.4 — Busca hierárquica (3 níveis)

Modificar `HybridSearchService`:

```
Nível 1 (filtro rápido): buscar nos sumários → identifica documentos relevantes
Nível 2 (precisão):      buscar nos chunks desses documentos → trechos exatos
Nível 3 (fallback):      se < 3 resultados, buscar em todos os chunks (atual)
```

**Benefício:** Reduz o espaço de busca e melhora a precisão sem sacrificar recall.

#### 2.5 — Estimativa

- 890 sumários de página (1 chamada LLM por página, batchável em 10)
- ~90 chamadas LLM + ~890 embeddings novos
- Tempo estimado: ~30 min (batch com rate limiting)

---

## Fase 3 — Enriquecimento de Metadados Estruturados

### Problema

As páginas têm `category` e `subcategory` extraídas do slug da URL, mas falta uma taxonomia normalizada. Não há extração de entidades (datas, valores, números de processo), o que impede filtros facetados e contexto preciso para o DataAgent.

### Solução

Enriquecer cada página/documento com metadados estruturados via NER + classificação LLM.

### Implementação

#### 3.1 — Modelo de dados

```sql
-- Migration: 007_enriched_metadata.py
CREATE TABLE page_entities (
    id            SERIAL PRIMARY KEY,
    page_id       INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    entity_type   TEXT NOT NULL,        -- 'date_range', 'monetary_value', 'process_number',
                                        -- 'contract_number', 'person', 'organization', 'cpf_cnpj'
    entity_value  TEXT NOT NULL,        -- Valor normalizado
    raw_text      TEXT,                 -- Texto original no documento
    confidence    REAL DEFAULT 1.0,     -- 0.0-1.0
    created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_pe_page ON page_entities(page_id);
CREATE INDEX idx_pe_type_value ON page_entities(entity_type, entity_value);

-- Tags semânticas normalizadas
CREATE TABLE page_tags (
    id            SERIAL PRIMARY KEY,
    page_id       INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    tag           TEXT NOT NULL,         -- Ex: 'licitacao', 'diaria', 'contrato', 'folha_pagamento'
    confidence    REAL DEFAULT 1.0,
    UNIQUE(page_id, tag)
);

CREATE INDEX idx_pt_tag ON page_tags(tag);
```

#### 3.2 — Serviço de extração

```
app/domain/services/metadata_enricher.py
```

**Duas etapas por página:**

**Etapa A — NER com regex (rápido, sem LLM):**
- Datas: `\d{2}/\d{2}/\d{4}`, `exercício \d{4}`, `\d{1,2}º semestre \d{4}`
- Valores: `R\$\s?[\d.,]+`, `\d+[\d.,]*\s*(mil|milhões|bilhões)`
- Processos: `\d{7}-\d{2}\.\d{4}\.\d\.\d{2}\.\d{4}` (numeração CNJ)
- Contratos: `Contrato\s+n[°º]?\s*\d+/\d{4}`
- Pregões: `Pregão\s+(Eletrônico|Presencial)\s+n[°º]?\s*\d+/\d{4}`
- CNPJ: `\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}`

**Etapa B — Classificação temática via LLM (batch):**

```
Classifique o documento abaixo em 1-3 categorias da lista:
[licitacao, contrato, convenio, diaria, passagem, folha_pagamento,
 relatorio_gestao, orcamento, receita, despesa, patrimonio,
 prestacao_contas, auditoria, ata, resolucao, portaria]

Título: {title}
Categoria URL: {category}
Primeiros 2000 caracteres: {content[:2000]}

Retorne JSON: {"tags": ["tag1", "tag2"], "periodo_referencia": "2024" ou null}
```

#### 3.3 — Integração com a busca

- `HybridSearchService` aceita filtros opcionais: `entity_type`, `tag`, `periodo`
- O DataAgent pode usar filtros de entidade nas suas tool calls
- Nova tool declaration:

```python
{
    "name": "filter_by_entity",
    "description": "Filtra documentos por entidade (número de contrato, período, valor)",
    "parameters": {
        "entity_type": {"type": "string", "enum": ["date_range", "contract_number", ...]},
        "entity_value": {"type": "string"}
    }
}
```

#### 3.4 — Estimativa

- Etapa A (regex): ~5 min para 890 páginas (CPU only)
- Etapa B (LLM): ~890 chamadas batcháveis → ~45 min
- Storage: ~5-20 entidades por página → ~10K-18K registros

---

## Fase 4 — Chunking Semântico

### Problema

O chunker atual usa janela fixa de 500 tokens com overlap de 50. Isso corta parágrafos no meio, separa tabelas de seus títulos, e mistura seções sem relação. Chunks de baixa qualidade degradam tanto FTS quanto busca semântica.

### Solução

Substituir o chunking por janela deslizante por chunking baseado na estrutura do documento (headings, parágrafos, tabelas).

### Implementação

#### 4.1 — Novo chunker

```
app/adapters/outbound/document_processor/semantic_chunker.py
```

**Algoritmo:**

```
1. Parse do HTML/texto em blocos estruturais:
   - Heading (h1-h6) → inicia novo chunk
   - Parágrafo (<p>) → acumula no chunk atual
   - Tabela (<table>) → chunk isolado com caption/headers como prefixo
   - Lista (<ul>/<ol>) → mantém junto se < 300 tokens, senão divide por item

2. Para cada bloco:
   - Se bloco < 100 tokens → merge com bloco anterior (evita micro-chunks)
   - Se bloco > 800 tokens → divide por sentença (nltk.sent_tokenize ou regex)
   - Alvo: 300-600 tokens por chunk

3. Prefixo contextual em cada chunk:
   "{breadcrumb} > {section_title}\n\n{chunk_text}"
   Exemplo: "Transparência > Licitações > Pregão 2024\n\nA empresa XYZ..."

4. Metadados por chunk:
   - section_title: heading mais recente
   - page_number: para PDFs
   - has_table: boolean
   - parent_chunk_id: para chunks divididos de um bloco maior
```

#### 4.2 — Tratamento especial para PDFs

Para PDFs processados pelo PyMuPDF:

```
1. Usar page boundaries como separadores naturais
2. Detectar headers por tamanho de fonte (fitz font_size > threshold)
3. Tabelas extraídas por find_tables() → chunk isolado com:
   - Caption (texto imediatamente acima da tabela)
   - Headers como primeira linha
   - Dados como linhas subsequentes
4. Rodapés e cabeçalhos repetidos → remover (regex por padrão repetido em >50% das páginas)
```

#### 4.3 — Migração dos chunks existentes

```bash
# Script de migração
python -m scripts.rechunk_documents --dry-run    # Mostra o que será alterado
python -m scripts.rechunk_documents --execute     # Reprocessa todos
python -m scripts.rechunk_documents --reembed     # Regera embeddings após rechunk
```

**Estratégia:**
1. Criar chunks novos com flag `version = 2`
2. Manter chunks antigos (`version = 1`) até validação
3. Trocar busca para `version = 2`
4. Deletar chunks `version = 1` após confirmação

#### 4.4 — Validação de qualidade

Após o rechunking, validar:

- Nenhum chunk < 50 tokens (exceto tabelas pequenas)
- Nenhum chunk > 1000 tokens
- Cobertura: todo o texto original está representado em pelo menos um chunk
- Distribuição de tamanhos (histograma) segue curva normal centrada em ~400 tokens

#### 4.5 — Estimativa

- Desenvolvimento do chunker: médio (precisa lidar com HTML heterogêneo)
- Reprocessamento: ~30 min para 890 páginas + documentos
- Re-embedding: ~1h (todos os chunks novos precisam de embedding)

---

## Fase 5 — Detecção e Correção de Dados Corrompidos

### Problema

PDFs de transparência frequentemente contêm artefatos de OCR, tabelas malformadas, e texto duplicado. Chunks com "lixo" degradam as respostas do LLM (garbage in, garbage out).

### Solução

Pipeline de validação que atribui um score de qualidade a cada chunk e quarentena os de baixa qualidade.

### Implementação

#### 5.1 — Score de qualidade por chunk

```
app/domain/services/chunk_quality_scorer.py
```

**Critérios (0.0 a 1.0 cada, média ponderada):**

| Critério | Peso | Como medir |
|----------|------|------------|
| Comprimento adequado | 0.15 | Penaliza < 50 tokens ou > 1000 tokens |
| Densidade de palavras reais | 0.25 | % de tokens que são palavras do dicionário PT-BR |
| Ausência de artefatos OCR | 0.20 | Ausência de: `\|{3,}`, `_{3,}`, `\.{5,}`, chars isolados repetidos |
| Coerência de sentenças | 0.15 | Ao menos 50% das linhas terminam com pontuação |
| Ausência de duplicação interna | 0.10 | Não ter >30% do texto repetido (headers de página, rodapés) |
| Presença de conteúdo informativo | 0.15 | Ao menos 1 substantivo/verbo relevante (não só stop words) |

**Score final:** média ponderada dos critérios. Threshold: `>= 0.5` para incluir na busca.

#### 5.2 — Modelo de dados

```sql
ALTER TABLE document_chunks ADD COLUMN quality_score REAL;
ALTER TABLE document_chunks ADD COLUMN quality_flags TEXT[];  -- Ex: ['low_density', 'ocr_artifacts']
ALTER TABLE document_chunks ADD COLUMN quarantined BOOLEAN DEFAULT false;

-- Mesma estrutura para page_chunks quando existir
CREATE INDEX idx_dc_quarantine ON document_chunks(quarantined) WHERE quarantined = true;
```

#### 5.3 — Normalização de tabelas

```
app/domain/services/table_normalizer.py
```

**Ações:**
- Normalizar headers: `VL. TOTAL` → `valor_total`, `Nº CONTRATO` → `numero_contrato`
- Dicionário de sinônimos para headers comuns em documentos TRE-PI
- Remover linhas totalmente vazias
- Detectar e remover linhas de subtotal/total duplicadas
- Padronizar formato de valores: `1.234,56` → float, datas → ISO 8601

#### 5.4 — Deduplicação

```
app/domain/services/deduplicator.py
```

**Estratégia:**
- Hash SHA-256 do texto normalizado (lowercase, sem espaços extras, sem pontuação)
- Agrupar chunks com hash idêntico → manter apenas o com melhor quality_score
- Para documentos: comparar `full_text` hash → marcar duplicatas
- Log em `data_inconsistencies` com tipo `duplicate_content`

#### 5.5 — CLI

```bash
python -m app.adapters.inbound.cli.quality_check --score      # Calcula scores
python -m app.adapters.inbound.cli.quality_check --quarantine  # Quarentena chunks ruins
python -m app.adapters.inbound.cli.quality_check --report      # Relatório de qualidade
python -m app.adapters.inbound.cli.quality_check --deduplicate # Remove duplicatas
```

#### 5.6 — Estimativa

- Score de qualidade: ~10 min para todos os chunks (CPU only, regex + contadores)
- Normalização de tabelas: ~5 min
- Deduplicação: ~5 min (hash comparison)
- Sem custo de LLM nesta fase

---

## Fase 6 — Cross-referência e Grafo de Conhecimento Leve

### Problema

Documentos de transparência se referenciam mutuamente (um contrato referencia uma licitação, que referencia um empenho). Hoje essas relações não são capturadas, e o chat responde de forma fragmentada sobre processos que têm múltiplos documentos relacionados.

### Solução

Extrair referências cruzadas entre documentos e criar um grafo leve de relações, permitindo que a busca traga documentos relacionados como contexto secundário.

### Implementação

#### 6.1 — Modelo de dados

```sql
-- Migration: 008_document_relations.py
CREATE TABLE document_relations (
    id              SERIAL PRIMARY KEY,
    source_page_id  INTEGER NOT NULL REFERENCES pages(id) ON DELETE CASCADE,
    target_page_id  INTEGER REFERENCES pages(id) ON DELETE SET NULL,
    target_url      TEXT,                   -- Caso o target não esteja na base
    relation_type   TEXT NOT NULL,          -- 'referencia', 'atualiza', 'substitui',
                                            -- 'complementa', 'origina', 'decorre_de'
    context         TEXT,                   -- Trecho onde a referência aparece
    entity_key      TEXT,                   -- Ex: 'Pregão 012/2024', 'Contrato 045/2023'
    confidence      REAL DEFAULT 1.0,
    created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_dr_source ON document_relations(source_page_id);
CREATE INDEX idx_dr_target ON document_relations(target_page_id);
CREATE INDEX idx_dr_entity ON document_relations(entity_key);
```

#### 6.2 — Extração de relações

```
app/domain/services/relation_extractor.py
```

**Duas estratégias:**

**Estratégia A — Baseada em entidades (usa output da Fase 3):**
- Agrupar páginas que compartilham o mesmo `entity_value` (ex: mesmo número de pregão)
- Criar relação `referencia` entre elas
- Confidence baseada na especificidade da entidade (número de contrato = alta, ano = baixa)

**Estratégia B — Baseada em links internos (já coletados pelo crawler):**
- A tabela `page_links` já existe com links entre páginas
- Classificar o tipo de relação pelo contexto do link:
  - Link em "ver contrato" → `origina`
  - Link em "atualizado por" → `atualiza`
  - Link em "ver também" → `complementa`

**Estratégia C — LLM para casos ambíguos (batch):**

```
Dados dois documentos relacionados, classifique a relação:

Documento A: {title_a} — {summary_a}
Documento B: {title_b} — {summary_b}
Contexto do link: {link_context}

Tipos possíveis: referencia, atualiza, substitui, complementa, origina, decorre_de
Retorne JSON: {"relation": "tipo", "confidence": 0.0-1.0}
```

#### 6.3 — Integração com a busca

Modificar `HybridSearchService`:

```python
async def search_with_relations(self, query: str, top_k: int = 5) -> SearchResult:
    # 1. Busca normal (FTS + semântica + RRF)
    primary_results = await self.search(query, top_k=top_k)

    # 2. Para os top-3 resultados, buscar documentos relacionados
    related_ids = await self.relation_repo.get_related(
        page_ids=[r.page_id for r in primary_results[:3]],
        max_per_page=2
    )

    # 3. Adicionar como contexto secundário (não compete no ranking)
    return SearchResult(
        primary=primary_results,
        related=related_results,  # Exibidos como "Documentos relacionados"
    )
```

#### 6.4 — Visualização no frontend

No card de resposta, adicionar seção "Documentos relacionados":

```html
<div class="related-documents">
  <h4>Documentos relacionados</h4>
  <ul>
    <li><a href="...">Pregão Eletrônico 012/2024</a> — origina este contrato</li>
    <li><a href="...">Ata de Registro de Preços</a> — complementa</li>
  </ul>
</div>
```

#### 6.5 — Estimativa

- Estratégia A (entidades): ~15 min, depende da Fase 3
- Estratégia B (links): ~10 min, usa dados já existentes em `page_links`
- Estratégia C (LLM): ~1h para relações ambíguas
- Total: ~2h de processamento, complexidade de desenvolvimento alta

---

## Dependências entre Fases

```
Fase 1 (Perguntas Sintéticas)  ←  Embeddings funcionando
         ↑
Fase 4 (Chunking Semântico)    →  Requer re-execução das Fases 1 e 2

Fase 2 (Sumarização)           ←  Nenhuma (campo já existe)

Fase 3 (Metadados)             ←  Nenhuma
         ↓
Fase 6 (Cross-referência)      ←  Entidades da Fase 3

Fase 5 (Qualidade)             ←  Nenhuma (pode rodar a qualquer momento)
```

**Observação:** Se a Fase 4 (Chunking Semântico) for executada, as Fases 1, 2 e 5 precisam ser re-executadas sobre os novos chunks. Por isso a Fase 4 está posicionada antes da 5 na ordem de execução — melhor rechunkar antes de calcular scores de qualidade.

---

## Métricas de Sucesso

Para cada fase, medir antes e depois:

| Métrica | Como medir | Alvo |
|---------|-----------|------|
| **Recall@5** | % de queries de teste onde a resposta correta está nos top-5 | > 85% |
| **MRR** (Mean Reciprocal Rank) | 1/posição do primeiro resultado relevante | > 0.7 |
| **Chunks por resposta** | Média de chunks usados pelo LLM | 3-5 (nem pouco, nem excesso) |
| **Qualidade percebida** | Avaliação manual de 50 queries padrão | > 4/5 |
| **Cobertura de entidades** | % de páginas com pelo menos 1 entidade extraída | > 80% |
| **Taxa de quarentena** | % de chunks com quality_score < 0.5 | < 15% |

### Conjunto de queries de teste (exemplos)

```
1. "Quanto o TRE-PI gastou com diárias em 2024?"
2. "Quais licitações estão abertas?"
3. "Qual o valor do contrato com a empresa X?"
4. "Onde encontro o relatório de gestão de 2023?"
5. "Quem são os gestores de contratos?"
6. "Qual o orçamento aprovado para 2025?"
7. "Existem convênios vigentes?"
8. "Quanto foi pago em passagens aéreas no último semestre?"
9. "Qual a remuneração dos servidores?"
10. "Onde vejo a prestação de contas anual?"
```

---

## Próximos Passos

1. Garantir que o pipeline de ingestão existente está funcional (document_contents, document_chunks, embeddings preenchidos)
2. Iniciar Fase 1 (Perguntas Sintéticas) — maior impacto com menor risco
3. Em paralelo, executar Fase 2 (Sumarização) — baixo esforço, campo já existe
4. Avaliar resultados com as 10 queries de teste antes de prosseguir
