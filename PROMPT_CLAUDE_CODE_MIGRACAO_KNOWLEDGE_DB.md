# PROMPT PARA CLAUDE CODE — Migração da Base de Conhecimento

Cole este prompt inteiro no Claude Code para que ele faça as alterações necessárias no projeto.

---

## Contexto do Projeto

Estou desenvolvendo o **TRE-PI Transparência Chat**, um assistente de IA que ajuda cidadãos a encontrar informações na seção de Transparência e Prestação de Contas do TRE-PI (https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas).

O projeto já foi parcialmente implementado seguindo o plano original em `PLANO_IMPLEMENTACAO_ASSISTENTE_TRANSPARENCIA_TREPI.md`. No plano original, a base de conhecimento era o arquivo `SKILL.md` (Markdown estático, parseado em memória). **Isso mudou.**

## O que já foi feito (nova etapa)

Foi criado e executado com sucesso o script Python `crawler.py` que:

1. **Fez crawling completo** do portal de Transparência do TRE-PI (site Plone)
2. **Descobriu todas as URLs** via sitemap.xml.gz + crawling BFS recursivo
3. **Extraiu conteúdo estruturado** de cada página: título, descrição, breadcrumb, conteúdo textual principal, links internos, documentos PDF/CSV associados, categoria, subcategoria, tipo de conteúdo
4. **Gerou a base de conhecimento** em 3 formatos:
   - `knowledge.db` — SQLite com FTS5 (busca full-text) — **este é o formato principal agora**
   - `knowledge.json` — export JSON para referência
   - `knowledge.md` — export Markdown (compatível com o formato antigo)

O arquivo `knowledge.db` está em `app/data/knowledge.db` e contém as seguintes tabelas:

```sql
-- Tabela principal: cada página/documento é uma entrada
CREATE TABLE pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    main_content TEXT DEFAULT '',        -- Texto limpo do conteúdo principal da página
    content_summary TEXT DEFAULT '',     -- Resumo (primeiros 500 chars)
    category TEXT DEFAULT '',            -- Ex: "Gestão de Pessoas", "Licitações, Contratos e Instrumentos de Cooperação"
    subcategory TEXT DEFAULT '',         -- Ex: "Recursos Humanos e Remuneração"
    content_type TEXT DEFAULT 'page',   -- page, pdf, csv, video, api, google_sheet, error
    depth INTEGER DEFAULT 0,            -- Profundidade na árvore de navegação
    parent_url TEXT DEFAULT '',          -- URL da página pai
    breadcrumb_json TEXT DEFAULT '[]',  -- JSON array: [{"title": "...", "url": "..."}, ...]
    tags_json TEXT DEFAULT '[]',
    last_modified TEXT DEFAULT '',
    extracted_at TEXT DEFAULT '',
    search_text TEXT DEFAULT ''          -- Concatenação de título+descrição+conteúdo para FTS
);

-- Links internos encontrados em cada página
CREATE TABLE page_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    target_url TEXT NOT NULL,
    link_title TEXT DEFAULT '',
    link_type TEXT DEFAULT 'internal'   -- internal, document, external
);

-- Documentos (PDFs, CSVs) associados a páginas
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_url TEXT NOT NULL,
    document_url TEXT NOT NULL,
    document_title TEXT DEFAULT '',
    document_type TEXT DEFAULT 'pdf'    -- pdf, csv, xlsx, video
);

-- Árvore de navegação
CREATE TABLE navigation_tree (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_url TEXT NOT NULL,
    child_url TEXT NOT NULL,
    child_title TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0
);

-- Busca full-text (FTS5)
CREATE VIRTUAL TABLE pages_fts USING fts5(
    url, title, description, main_content, category, subcategory, tags,
    content='pages', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
```

## O que você precisa fazer

Altere o projeto para **substituir o módulo `knowledge_base.py`** (que lia o SKILL.md) por um novo módulo que usa o **SQLite `knowledge.db`** como fonte de dados. As alterações devem afetar os seguintes arquivos:

### 1. `app/services/knowledge_base.py` — REESCREVER COMPLETAMENTE

Substituir a lógica de parsing do Markdown por consultas ao SQLite. O novo módulo deve:

```python
import sqlite3
import json
from pathlib import Path

class KnowledgeBase:
    """
    Base de conhecimento do portal de Transparência do TRE-PI.
    Usa SQLite com FTS5 para busca full-text.
    """
    
    def __init__(self, db_path: str = None):
        """
        Inicializa a conexão com o banco.
        db_path padrão: app/data/knowledge.db
        """
        if db_path is None:
            db_path = str(Path(__file__).parent.parent / "data" / "knowledge.db")
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
    
    def search(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Busca páginas relevantes usando FTS5.
        Retorna lista de dicts com: url, title, description, content_summary,
        category, subcategory, content_type, breadcrumb, documents.
        
        A busca deve:
        - Normalizar acentos (unicode61 já faz isso no FTS5)
        - Tokenizar a query em palavras e conectar com OR
        - Ordenar por ranking BM25 do FTS5
        - Para cada resultado, incluir os documentos associados (da tabela documents)
        - Para cada resultado, incluir o breadcrumb parseado do JSON
        """
        ...
    
    def get_page_with_context(self, url: str) -> dict | None:
        """
        Retorna uma página específica com contexto completo:
        - Dados da página (title, description, content_summary, main_content)
        - Documentos associados (PDFs, CSVs)
        - Links filhos (subpáginas)
        - Páginas irmãs (mesmo parent_url)
        - Breadcrumb
        """
        ...
    
    def get_categories(self) -> list[dict]:
        """
        Retorna lista de categorias com contagem.
        Usado para sugestões iniciais e navegação.
        Ex: [{"category": "Gestão de Pessoas", "count": 45}, ...]
        """
        ...
    
    def get_suggestions(self) -> list[str]:
        """
        Retorna sugestões de perguntas frequentes baseadas
        nas categorias mais populares da base.
        """
        ...
    
    def get_stats(self) -> dict:
        """
        Estatísticas da base para o endpoint /api/health.
        Total de páginas, documentos, categorias, etc.
        """
        ...
```

### 2. `app/services/chat_engine.py` — ADAPTAR

Alterar o `ChatEngine` para:

- Usar o novo `KnowledgeBase.search()` que retorna dicts ricos (com documentos, breadcrumb)
- Montar o contexto para o prompt do Gemini incluindo:
  - As páginas encontradas com URL, título, resumo e tipo
  - Os documentos associados (PDFs/CSVs) com URLs diretas
  - O breadcrumb para dar noção de onde a informação está na árvore
- Quando a busca retornar uma página com `content_type != 'page'` (pdf, csv, video), NÃO tentar buscar conteúdo via content_fetcher — apenas informar o link
- Quando a busca retornar uma página com `main_content` já disponível no banco (foi extraído pelo crawler), usar esse conteúdo diretamente SEM precisar do content_fetcher, economizando uma request ao site
- O content_fetcher só deve ser acionado quando:
  - A página está no banco mas `main_content` está vazio
  - Ou quando o usuário pede explicitamente informações detalhadas de uma página específica e o content_summary do banco não é suficiente

### 3. `app/services/content_fetcher.py` — AJUSTAR

- Antes de fazer request HTTP ao site, verificar se o conteúdo já está no `knowledge.db`
- Adicionar método `get_from_db_or_fetch(url)` que:
  1. Consulta o banco primeiro (`knowledge_base.get_page_with_context(url)`)
  2. Se `main_content` existir no banco, retorna sem fazer HTTP
  3. Só faz scraping se o conteúdo não estiver no banco

### 4. `app/prompts/system_prompt.py` — AJUSTAR

O template do system prompt deve ser ajustado para usar o contexto mais rico que agora vem do SQLite. O bloco de "Páginas relevantes" no prompt deve incluir:

```
## Páginas relevantes encontradas na base de conhecimento

### 1. {title}
- **URL:** {url}
- **Categoria:** {category} > {subcategory}
- **Tipo:** {content_type}
- **Resumo:** {content_summary}
- **Documentos associados:**
  - 📄 {doc_title} → {doc_url} (tipo: {doc_type})
- **Caminho de navegação:** {breadcrumb formatado}

### 2. {title}
...
```

Isso dá ao Gemini muito mais contexto para responder com precisão e fornecer os links corretos.

### 5. `app/routers/chat.py` — AJUSTAR

- O endpoint `GET /api/suggest` deve chamar `knowledge_base.get_suggestions()` ao invés de retornar uma lista fixa
- O endpoint `GET /api/health` deve incluir `knowledge_base.get_stats()` na resposta
- Adicionar novo endpoint `GET /api/categories` que retorna a árvore de categorias (útil para o frontend mostrar temas disponíveis)

### 6. `app/config.py` — ADICIONAR

Adicionar variável de configuração:

```python
KNOWLEDGE_DB_PATH = os.getenv("KNOWLEDGE_DB_PATH", "app/data/knowledge.db")
```

### 7. `app/main.py` — AJUSTAR

Na inicialização do FastAPI:

```python
@app.on_event("startup")
async def startup():
    # Verificar se knowledge.db existe
    db_path = config.KNOWLEDGE_DB_PATH
    if not Path(db_path).exists():
        logger.error(f"Base de conhecimento não encontrada: {db_path}")
        raise FileNotFoundError(f"knowledge.db não encontrado em {db_path}")
    
    # Inicializar KnowledgeBase
    app.state.knowledge_base = KnowledgeBase(db_path)
    stats = app.state.knowledge_base.get_stats()
    logger.info(f"Base de conhecimento carregada: {stats['total_pages']} páginas, {stats['total_documents']} documentos")
```

## Regras importantes

1. **NÃO remover o content_fetcher.py** — ele ainda é necessário como fallback para páginas sem conteúdo no banco e para busca em tempo real quando o usuário pede detalhes
2. **O `knowledge.db` é READ-ONLY no runtime** — ele só é escrito pelo crawler.py (que roda separadamente). O aplicativo web nunca escreve nele
3. **Thread safety:** usar `check_same_thread=False` no sqlite3.connect pois o FastAPI é assíncrono. Considerar usar um pool de conexões ou abrir/fechar conexões por request se houver problemas
4. **O arquivo `knowledge.md` (SKILL.md antigo) pode ser mantido como backup** mas não é mais a fonte primária
5. **Manter compatibilidade com o frontend** — os schemas de resposta (ChatResponse, LinkItem, etc.) não devem mudar, apenas o conteúdo fica mais rico
6. **Testar a busca FTS5** com queries em português, com e sem acentos. Exemplos de teste:
   - "licitações em andamento" → deve encontrar a seção de pregões
   - "salário servidores" → deve encontrar remuneração/folha de pagamento
   - "LGPD proteção dados" → deve encontrar a seção LGPD
   - "auditoria TCU" → deve encontrar relatórios de auditoria
   - "contratos tecnologia informação" → deve encontrar contratos de TI

## Estrutura de pastas esperada após as alterações

```
tre-pi-transparencia-chat/
├── app/
│   ├── __init__.py
│   ├── main.py                     # ← AJUSTAR (startup com knowledge.db)
│   ├── config.py                   # ← AJUSTAR (adicionar KNOWLEDGE_DB_PATH)
│   ├── routers/
│   │   ├── __init__.py
│   │   └── chat.py                 # ← AJUSTAR (novos endpoints, usar KB do SQLite)
│   ├── services/
│   │   ├── __init__.py
│   │   ├── knowledge_base.py       # ← REESCREVER (SQLite + FTS5)
│   │   ├── chat_engine.py          # ← AJUSTAR (usar KB rico, menos content_fetcher)
│   │   ├── content_fetcher.py      # ← AJUSTAR (verificar banco antes de HTTP)
│   │   └── vertex_client.py        # (sem alterações)
│   ├── models/
│   │   ├── __init__.py
│   │   └── schemas.py              # (sem alterações nos schemas de resposta)
│   ├── prompts/
│   │   └── system_prompt.py        # ← AJUSTAR (contexto mais rico)
│   └── data/
│       ├── knowledge.db            # ← NOVA FONTE PRIMÁRIA (SQLite com FTS5)
│       ├── knowledge.json          # ← backup/referência
│       └── knowledge.md            # ← backup (antigo SKILL.md)
├── crawler.py                      # Script de crawling (já implementado, não alterar)
├── static/                         # Frontend (sem alterações nesta tarefa)
├── Containerfile
├── requirements.txt                # ← Garantir que NÃO precisa de novas dependências
│                                   #    (sqlite3 é built-in do Python)
└── README.md
```

## Resumo executivo

**Antes:** knowledge_base.py parseava um Markdown estático (SKILL.md) e fazia busca por keywords simples em memória.

**Depois:** knowledge_base.py consulta um SQLite com ~300 páginas indexadas via FTS5, com conteúdo textual já extraído, documentos associados, breadcrumb, categorias e árvore de navegação. A busca é mais precisa, o contexto enviado ao Gemini é mais rico, e o content_fetcher é acionado com muito menos frequência (só quando o conteúdo do banco não é suficiente).

Faça todas as alterações descritas acima, mantendo o código limpo, bem documentado com docstrings em português, e com type hints. Teste a busca FTS5 com os exemplos fornecidos.
