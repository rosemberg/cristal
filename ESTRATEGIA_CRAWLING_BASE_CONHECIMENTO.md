# Estratégia de Crawling e Construção da Base de Conhecimento
## Assistente de Transparência — TRE-PI

**Data:** 2026-03-12  
**Complemento ao:** PLANO_IMPLEMENTACAO_ASSISTENTE_TRANSPARENCIA_TREPI.md  
**URL base:** https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas

---

## 1. Análise da Plataforma

### 1.1 CMS identificado: Plone

O site do TRE-PI roda sobre **Plone** (CMS Python). Isso é fundamental porque o Plone expõe endpoints nativos que facilitam enormemente o mapeamento da estrutura sem precisar fazer crawling "cego" por HTML:

| Endpoint Plone | O que retorna | Utilidade |
|---|---|---|
| `/sitemap.xml.gz` | Mapa completo de todas as URLs publicadas | Descoberta de 100% das páginas |
| `/@@search?SearchableText=...` | Busca interna do portal | Validação e busca complementar |
| `/@navigation` (se REST API ativa) | Árvore de navegação em JSON | Estrutura hierárquica nativa |
| `/folder_contents` | Listagem de itens dentro de uma pasta | Descoberta de subpáginas |
| `?portal_type=File` | Filtragem por tipo de conteúdo | Encontrar PDFs, CSVs, etc. |

### 1.2 Padrão de URLs observado

A estrutura de URLs segue um padrão hierárquico previsível:

```
/transparencia-e-prestacao-de-contas/                          ← Raiz (nível 0)
  ├── gestao-de-pessoas/                                        ← Categoria (nível 1)
  │   ├── estrutura-remuneratoria-dos-cargos-efetivos          ← Página (nível 2)
  │   ├── recursos-humanos-e-remuneracao/                      ← Subcategoria (nível 2)
  │   │   ├── detalhamento-da-folha-de-pagamento...            ← Página (nível 3)
  │   │   └── diarias-e-passagens-1/                           ← Subpasta (nível 3)
  │   │       └── arquivos-diarias/                            ← Pasta de docs (nível 4)
  │   │           └── tre-pi-diarias-junho-2022.pdf            ← Documento
  │   └── credenciamentos-medicos-e-odontologicos/
  │       └── termos-de-credenciamento
  ├── licitacoes-e-contratos/
  │   ├── licitacoes/
  │   │   ├── pregoes/
  │   │   │   ├── licitacoes-em-andamento
  │   │   │   └── licitacoes-concluidas
  │   │   ├── concorrencia
  │   │   └── tomada-de-preco
  │   ├── contratos/
  │   │   ├── contratos-de-t-i
  │   │   └── atas-de-registros-de-preco
  │   └── outras-contratacoes/
  │       ├── convenios
  │       ├── termo-acordo-de-cooperacao-tecnica
  │       └── ato-concertado
  ├── governanca/
  │   ├── estrategia/
  │   ├── governanca-de-tecnologia-da-informacao/
  │   └── accountability/
  └── ...
```

**Profundidade máxima observada:** 5 a 6 níveis.

### 1.3 Tipos de conteúdo encontrados

| Tipo | Extensão/Padrão | Quantidade estimada | Tratamento |
|---|---|---|---|
| Página HTML (Plone Page) | sem extensão | ~250 | Extrair texto + links internos |
| PDF | `.pdf` | ~50+ | Registrar metadados, não extrair conteúdo |
| CSV | `.csv` | ~20+ | Registrar metadados |
| Link externo (YouTube, etc.) | URLs externas | ~10 | Registrar como mídia |
| Swagger/API | padrão swagger | 1 | Registrar como API |
| Google Planilhas | docs.google.com | ~5 | Registrar como link externo |

---

## 2. Estratégia de Crawling — Abordagem em 3 Fases

### Visão geral

```
FASE 1: Descoberta (Sitemap + Navegação)
    ↓ Gera lista completa de URLs
FASE 2: Extração (Crawling profundo com BeautifulSoup)
    ↓ Extrai conteúdo, metadados, links, breadcrumb, documentos
FASE 3: Enriquecimento (Classificação + Contexto semântico)
    ↓ Gera base de conhecimento final (SQLite + JSON)
```

---

### FASE 1 — Descoberta de URLs

**Objetivo:** Obter a lista completa e não-duplicada de todas as páginas e documentos sob `/transparencia-e-prestacao-de-contas/`.

#### 1.1 Fonte primária: Sitemap do Plone

```python
import gzip
import httpx
from xml.etree import ElementTree

async def fetch_sitemap_urls(base_url: str) -> list[str]:
    """
    Plone gera automaticamente /sitemap.xml.gz com TODAS as URLs publicadas.
    Esta é a fonte mais confiável e completa.
    """
    sitemap_urls_to_try = [
        f"{base_url}/sitemap.xml.gz",
        f"{base_url}/sitemap.xml",
        "https://www.tre-pi.jus.br/sitemap.xml.gz",
        "https://www.tre-pi.jus.br/sitemap.xml",
    ]
    
    all_urls = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for sitemap_url in sitemap_urls_to_try:
            try:
                resp = await client.get(sitemap_url)
                if resp.status_code == 200:
                    content = resp.content
                    if sitemap_url.endswith('.gz'):
                        content = gzip.decompress(content)
                    
                    root = ElementTree.fromstring(content)
                    ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}
                    
                    for url_elem in root.findall('.//sm:url', ns):
                        loc = url_elem.find('sm:loc', ns)
                        lastmod = url_elem.find('sm:lastmod', ns)
                        if loc is not None:
                            url = loc.text.strip()
                            # Filtrar apenas URLs da seção de transparência
                            if '/transparencia-e-prestacao-de-contas' in url:
                                all_urls.append({
                                    'url': url,
                                    'lastmod': lastmod.text if lastmod is not None else None
                                })
                    break  # Usou o primeiro sitemap que funcionou
            except Exception:
                continue
    
    return all_urls
```

#### 1.2 Fonte complementar: Crawling recursivo por links

O sitemap pode não incluir tudo (especialmente documentos PDF/CSV que são "arquivos" no Plone, não "páginas"). Então complementamos com crawling recursivo:

```python
from urllib.parse import urljoin, urlparse
from collections import deque

async def crawl_discover_urls(
    start_url: str,
    allowed_prefix: str = "/transparencia-e-prestacao-de-contas",
    max_pages: int = 500,
    max_depth: int = 7,
    delay: float = 0.5  # Respeitar o servidor
) -> dict[str, dict]:
    """
    BFS (Breadth-First Search) a partir da raiz.
    Descobre URLs não listadas no sitemap, especialmente documentos.
    """
    visited = {}
    queue = deque([(start_url, 0, None)])  # (url, depth, parent_url)
    
    async with httpx.AsyncClient(
        timeout=15, 
        follow_redirects=True,
        headers={'User-Agent': 'TRE-PI-TransparenciaBot/1.0'}
    ) as client:
        while queue and len(visited) < max_pages:
            url, depth, parent = queue.popleft()
            
            if url in visited or depth > max_depth:
                continue
            
            # Verificar se está no escopo
            parsed = urlparse(url)
            if parsed.netloc not in ('', 'www.tre-pi.jus.br'):
                continue
            if allowed_prefix not in parsed.path:
                continue
            
            try:
                # HEAD primeiro para documentos (economiza bandwidth)
                head_resp = await client.head(url)
                content_type = head_resp.headers.get('content-type', '')
                
                visited[url] = {
                    'url': url,
                    'depth': depth,
                    'parent_url': parent,
                    'content_type': _classify_content_type(url, content_type),
                    'status_code': head_resp.status_code,
                    'discovered_via': 'crawl'
                }
                
                # Se for HTML, fazer GET para extrair links filhos
                if 'text/html' in content_type:
                    await asyncio.sleep(delay)
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        links = _extract_links(resp.text, url)
                        for link in links:
                            if link not in visited:
                                queue.append((link, depth + 1, url))
                
            except Exception as e:
                visited[url] = {
                    'url': url, 'depth': depth, 'parent_url': parent,
                    'content_type': 'error', 'error': str(e),
                    'discovered_via': 'crawl'
                }
    
    return visited


def _extract_links(html: str, base_url: str) -> list[str]:
    """Extrai todos os links <a href> relevantes de uma página."""
    from bs4 import BeautifulSoup
    
    soup = BeautifulSoup(html, 'html.parser')
    links = set()
    
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href'].strip()
        
        # Ignorar âncoras, javascript, mailto
        if href.startswith(('#', 'javascript:', 'mailto:', 'tel:')):
            continue
        
        # Resolver URLs relativas
        full_url = urljoin(base_url, href)
        
        # Remover fragmentos e query strings desnecessárias
        parsed = urlparse(full_url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        
        # Manter apenas URLs do domínio e escopo
        if 'tre-pi.jus.br' in parsed.netloc:
            if '/transparencia-e-prestacao-de-contas' in parsed.path:
                links.add(clean_url)
            # Também capturar links para documentos (@@display-file, etc.)
            elif '@@display-file' in parsed.path or parsed.path.endswith(('.pdf', '.csv', '.xlsx')):
                links.add(full_url)
    
    return list(links)


def _classify_content_type(url: str, http_content_type: str) -> str:
    """Classifica o tipo de conteúdo pela URL e Content-Type."""
    url_lower = url.lower()
    
    if url_lower.endswith('.pdf') or '@@display-file' in url_lower:
        return 'pdf'
    if url_lower.endswith('.csv'):
        return 'csv'
    if url_lower.endswith(('.xlsx', '.xls')):
        return 'spreadsheet'
    if 'youtube.com' in url_lower or 'youtu.be' in url_lower:
        return 'video'
    if 'docs.google.com/spreadsheets' in url_lower:
        return 'google_sheet'
    if 'swagger' in url_lower:
        return 'api'
    if 'text/html' in http_content_type:
        return 'page'
    if 'application/pdf' in http_content_type:
        return 'pdf'
    
    return 'unknown'
```

#### 1.3 Merge das duas fontes

```python
async def discover_all_urls(base_url: str) -> list[dict]:
    """
    Combina sitemap + crawling para cobertura máxima.
    Retorna lista deduplicada e classificada.
    """
    # 1. URLs do sitemap (rápido, completo para páginas)
    sitemap_urls = await fetch_sitemap_urls(base_url)
    
    # 2. URLs do crawling (descobre documentos e links ocultos)
    crawled = await crawl_discover_urls(base_url)
    
    # 3. Merge com deduplicação
    all_urls = {}
    
    for item in sitemap_urls:
        url = _normalize_url(item['url'])
        all_urls[url] = {
            **item,
            'url': url,
            'discovered_via': 'sitemap'
        }
    
    for url, data in crawled.items():
        normalized = _normalize_url(url)
        if normalized not in all_urls:
            all_urls[normalized] = data
        else:
            # Enriquecer com dados do crawling
            all_urls[normalized].update({
                'depth': data.get('depth'),
                'parent_url': data.get('parent_url'),
                'content_type': data.get('content_type', all_urls[normalized].get('content_type')),
            })
    
    return list(all_urls.values())


def _normalize_url(url: str) -> str:
    """Remove trailing slashes, fragmentos e normaliza."""
    parsed = urlparse(url)
    path = parsed.path.rstrip('/')
    return f"{parsed.scheme}://{parsed.netloc}{path}"
```

---

### FASE 2 — Extração de Conteúdo e Metadados

**Objetivo:** Para cada URL descoberta, extrair informações que servirão de contexto para o assistente.

#### 2.1 Extração de páginas HTML

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class PageData:
    url: str
    title: str = ""
    description: str = ""
    breadcrumb: list[dict] = field(default_factory=list)  # [{title, url}, ...]
    main_content: str = ""          # Texto limpo do conteúdo principal
    content_summary: str = ""       # Resumo gerado (primeiros 500 chars)
    internal_links: list[dict] = field(default_factory=list)  # Links dentro da página
    documents: list[dict] = field(default_factory=list)       # PDFs/CSVs linkados
    category: str = ""              # Derivada da URL (nível 1)
    subcategory: str = ""           # Derivada da URL (nível 2)
    content_type: str = "page"
    depth: int = 0
    parent_url: str = ""
    tags: list[str] = field(default_factory=list)  # Tags Plone (se disponíveis)
    last_modified: str = ""
    extracted_at: str = ""


async def extract_page_data(url: str, client: httpx.AsyncClient) -> PageData:
    """
    Extrai dados estruturados de uma página HTML do portal Plone do TRE-PI.
    """
    page = PageData(url=url, extracted_at=datetime.now().isoformat())
    
    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            page.content_type = 'error'
            return page
        
        content_type = resp.headers.get('content-type', '')
        if 'text/html' not in content_type:
            page.content_type = _classify_content_type(url, content_type)
            return page
        
        soup = BeautifulSoup(resp.text, 'html.parser')
        
        # 1. TÍTULO
        # Plone usa <h1 class="documentFirstHeading"> ou <title>
        h1 = soup.find('h1', class_='documentFirstHeading')
        if h1:
            page.title = h1.get_text(strip=True)
        else:
            title_tag = soup.find('title')
            if title_tag:
                page.title = title_tag.get_text(strip=True).split('—')[0].strip()
        
        # 2. DESCRIÇÃO
        # Plone: <div class="documentDescription">
        desc_div = soup.find('div', class_='documentDescription')
        if desc_div:
            page.description = desc_div.get_text(strip=True)
        else:
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc:
                page.description = meta_desc.get('content', '')
        
        # 3. BREADCRUMB
        # Plone: <ol id="portal-breadcrumbs"> ou <nav class="breadcrumb">
        breadcrumb_nav = soup.find('ol', id='portal-breadcrumbs') or soup.find('nav', class_='breadcrumb')
        if breadcrumb_nav:
            for li in breadcrumb_nav.find_all('li'):
                a_tag = li.find('a')
                if a_tag:
                    page.breadcrumb.append({
                        'title': a_tag.get_text(strip=True),
                        'url': urljoin(url, a_tag.get('href', ''))
                    })
                else:
                    text = li.get_text(strip=True)
                    if text:
                        page.breadcrumb.append({'title': text, 'url': url})
        
        # 4. CONTEÚDO PRINCIPAL
        # Plone: <div id="content-core"> é o conteúdo editado
        content_core = soup.find('div', id='content-core')
        if not content_core:
            content_core = soup.find('article') or soup.find('div', id='content')
        
        if content_core:
            # Remover scripts, styles, nav
            for tag in content_core.find_all(['script', 'style', 'nav', 'footer']):
                tag.decompose()
            
            page.main_content = content_core.get_text(separator='\n', strip=True)
            page.content_summary = page.main_content[:500].rsplit(' ', 1)[0] + '...' \
                if len(page.main_content) > 500 else page.main_content
        
        # 5. LINKS INTERNOS (subpáginas e itens relacionados)
        if content_core:
            for a_tag in content_core.find_all('a', href=True):
                href = urljoin(url, a_tag['href'])
                text = a_tag.get_text(strip=True)
                if 'tre-pi.jus.br' in href and text:
                    is_doc = any(href.lower().endswith(ext) for ext in ('.pdf', '.csv', '.xlsx'))
                    is_doc = is_doc or '@@display-file' in href
                    
                    entry = {'title': text, 'url': href}
                    if is_doc:
                        entry['type'] = _classify_content_type(href, '')
                        page.documents.append(entry)
                    else:
                        page.internal_links.append(entry)
        
        # 6. CATEGORIA e SUBCATEGORIA (derivadas da URL)
        path_parts = urlparse(url).path.split('/')
        # /transparencia-e-prestacao-de-contas/gestao-de-pessoas/...
        tp_index = next((i for i, p in enumerate(path_parts) 
                        if p == 'transparencia-e-prestacao-de-contas'), -1)
        if tp_index >= 0 and tp_index + 1 < len(path_parts):
            page.category = _slug_to_title(path_parts[tp_index + 1])
        if tp_index >= 0 and tp_index + 2 < len(path_parts):
            page.subcategory = _slug_to_title(path_parts[tp_index + 2])
        
        # 7. TAGS do Plone (se disponíveis)
        for meta_tag in soup.find_all('meta', attrs={'name': 'keywords'}):
            keywords = meta_tag.get('content', '')
            page.tags = [k.strip() for k in keywords.split(',') if k.strip()]
        
        # 8. ÚLTIMA MODIFICAÇÃO
        last_mod = soup.find('meta', attrs={'name': 'DC.date.modified'})
        if last_mod:
            page.last_modified = last_mod.get('content', '')
    
    except Exception as e:
        page.content_type = 'error'
        page.description = f"Erro na extração: {str(e)}"
    
    return page


def _slug_to_title(slug: str) -> str:
    """Converte slug de URL para título legível."""
    SLUG_MAP = {
        'gestao-de-pessoas': 'Gestão de Pessoas',
        'licitacoes-e-contratos': 'Licitações, Contratos e Instrumentos de Cooperação',
        'governanca': 'Gestão e Governança',
        'gestao-orcamentaria-e-financeira': 'Gestão Orçamentária e Financeira',
        'planos-de-auditoria-interna': 'Auditoria',
        'audiencias-publicas': 'Audiências e Sessões',
        'colegiados': 'Colegiados',
        'contabilidade': 'Contabilidade',
        'estatistica-processual': 'Estatística Processual',
        'gestao-patrimonial-e-infraestrutura': 'Gestão Patrimonial e Infraestrutura',
        'lei-de-acesso-a-informacao-declaracao-anual': 'Lei de Acesso à Informação',
        'lgpd-lei-geral-de-protecao-de-dados': 'LGPD - Proteção de Dados',
        'ouvidoria': 'Ouvidoria',
        'prestacao-de-constas-da-gestao': 'Prestação de Contas da Gestão',
        'sei': 'SEI - Sistema Eletrônico de Informações',
        'servico-de-informacoes-ao-cidadao-sic': 'Serviço de Informação ao Cidadão (SIC)',
        'sustentabililidade_acessibilidade_inclusao': 'Sustentabilidade, Acessibilidade e Inclusão',
        'tecnologia-da-informacao-e-comunicacao-1': 'Tecnologia da Informação',
        'transparencia-cnj': 'Transparência CNJ',
        'relatorios-cnj': 'Relatórios CNJ',
        'relatorios-tre-pi': 'Relatórios TRE-PI',
    }
    return SLUG_MAP.get(slug, slug.replace('-', ' ').replace('_', ' ').title())
```

#### 2.2 Extração em lote com controle de carga

```python
import asyncio
from typing import AsyncIterator

async def extract_all_pages(
    urls: list[dict],
    concurrency: int = 3,        # Máximo de requests simultâneos
    delay_between: float = 0.5,  # Delay entre requests (respeitar servidor)
    progress_callback=None       # Para feedback de progresso
) -> list[PageData]:
    """
    Extrai conteúdo de todas as URLs com rate limiting responsável.
    
    IMPORTANTE: O servidor é do TRE-PI (nosso próprio órgão).
    Mesmo assim, ser gentil com a carga:
    - Máximo 3 requests simultâneos
    - 500ms entre cada request
    - User-Agent identificando o bot
    - Respeitar robots.txt
    """
    semaphore = asyncio.Semaphore(concurrency)
    results = []
    total = len(urls)
    
    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=True,
        headers={
            'User-Agent': 'TRE-PI-TransparenciaBot/1.0 (+transparencia-chat)',
            'Accept': 'text/html,application/xhtml+xml',
        }
    ) as client:
        
        async def process_url(idx: int, url_data: dict):
            async with semaphore:
                await asyncio.sleep(delay_between)
                url = url_data['url']
                ct = url_data.get('content_type', 'page')
                
                if ct in ('pdf', 'csv', 'spreadsheet', 'video', 'google_sheet', 'api'):
                    # Não fazer GET em documentos — apenas registrar metadados
                    page = PageData(
                        url=url,
                        content_type=ct,
                        title=url_data.get('title', url.split('/')[-1]),
                        category=url_data.get('category', ''),
                        extracted_at=datetime.now().isoformat()
                    )
                else:
                    page = await extract_page_data(url, client)
                
                # Enriquecer com dados da fase de descoberta
                page.depth = url_data.get('depth', 0)
                page.parent_url = url_data.get('parent_url', '')
                
                if progress_callback:
                    progress_callback(idx + 1, total, url)
                
                return page
        
        tasks = [process_url(i, u) for i, u in enumerate(urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filtrar erros
    return [r for r in results if isinstance(r, PageData)]
```

---

### FASE 3 — Construção da Base de Conhecimento

**Objetivo:** Transformar os dados extraídos em uma base estruturada, otimizada para consultas do assistente.

#### 3.1 Esquema do banco de dados (SQLite)

```sql
-- Tabela principal: cada página/documento é uma entrada
CREATE TABLE pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    main_content TEXT DEFAULT '',
    content_summary TEXT DEFAULT '',
    category TEXT DEFAULT '',
    subcategory TEXT DEFAULT '',
    content_type TEXT DEFAULT 'page',  -- page, pdf, csv, video, api, error
    depth INTEGER DEFAULT 0,
    parent_url TEXT DEFAULT '',
    breadcrumb_json TEXT DEFAULT '[]',  -- JSON array
    tags_json TEXT DEFAULT '[]',        -- JSON array
    last_modified TEXT DEFAULT '',
    extracted_at TEXT DEFAULT '',
    
    -- Campos para busca full-text
    search_text TEXT DEFAULT ''  -- title + description + content + tags concatenados
);

-- Links internos encontrados em cada página
CREATE TABLE page_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,          -- Página onde o link foi encontrado
    target_url TEXT NOT NULL,          -- Para onde o link aponta
    link_title TEXT DEFAULT '',        -- Texto do link
    link_type TEXT DEFAULT 'internal', -- internal, document, external
    FOREIGN KEY (source_url) REFERENCES pages(url)
);

-- Documentos (PDFs, CSVs, etc.) associados a páginas
CREATE TABLE documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_url TEXT NOT NULL,            -- Página que referencia este documento
    document_url TEXT NOT NULL,        -- URL direta do documento
    document_title TEXT DEFAULT '',
    document_type TEXT DEFAULT 'pdf',  -- pdf, csv, xlsx, video
    context TEXT DEFAULT '',           -- Texto ao redor do link (contexto)
    FOREIGN KEY (page_url) REFERENCES pages(url)
);

-- Árvore de navegação (relações pai-filho)
CREATE TABLE navigation_tree (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_url TEXT NOT NULL,
    child_url TEXT NOT NULL,
    child_title TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    UNIQUE(parent_url, child_url)
);

-- Índice FTS5 para busca full-text em português
CREATE VIRTUAL TABLE pages_fts USING fts5(
    url,
    title,
    description,
    main_content,
    category,
    subcategory,
    tags,
    content='pages',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- Triggers para manter FTS sincronizado
CREATE TRIGGER pages_ai AFTER INSERT ON pages BEGIN
    INSERT INTO pages_fts(rowid, url, title, description, main_content, category, subcategory, tags)
    VALUES (new.id, new.url, new.title, new.description, new.main_content, new.category, new.subcategory, new.tags_json);
END;

-- Índices para consultas frequentes
CREATE INDEX idx_pages_category ON pages(category);
CREATE INDEX idx_pages_content_type ON pages(content_type);
CREATE INDEX idx_pages_depth ON pages(depth);
CREATE INDEX idx_page_links_source ON page_links(source_url);
CREATE INDEX idx_page_links_target ON page_links(target_url);
CREATE INDEX idx_documents_page ON documents(page_url);
CREATE INDEX idx_nav_parent ON navigation_tree(parent_url);
```

#### 3.2 Alimentação do banco

```python
import sqlite3
import json

class KnowledgeDB:
    def __init__(self, db_path: str = "knowledge.db"):
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()
    
    def _create_tables(self):
        """Cria todas as tabelas (idempotente)."""
        self.conn.executescript(SCHEMA_SQL)  # O SQL acima
    
    def insert_page(self, page: PageData):
        """Insere uma página extraída no banco."""
        self.conn.execute("""
            INSERT OR REPLACE INTO pages 
            (url, title, description, main_content, content_summary,
             category, subcategory, content_type, depth, parent_url,
             breadcrumb_json, tags_json, last_modified, extracted_at, search_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            page.url,
            page.title,
            page.description,
            page.main_content,
            page.content_summary,
            page.category,
            page.subcategory,
            page.content_type,
            page.depth,
            page.parent_url,
            json.dumps(page.breadcrumb, ensure_ascii=False),
            json.dumps(page.tags, ensure_ascii=False),
            page.last_modified,
            page.extracted_at,
            # search_text: concatenação para busca
            f"{page.title} {page.description} {page.main_content} {' '.join(page.tags)}".lower()
        ))
        
        # Links internos
        for link in page.internal_links:
            self.conn.execute("""
                INSERT OR IGNORE INTO page_links (source_url, target_url, link_title, link_type)
                VALUES (?, ?, ?, 'internal')
            """, (page.url, link['url'], link['title']))
        
        # Documentos
        for doc in page.documents:
            self.conn.execute("""
                INSERT OR IGNORE INTO documents (page_url, document_url, document_title, document_type)
                VALUES (?, ?, ?, ?)
            """, (page.url, doc['url'], doc['title'], doc.get('type', 'pdf')))
        
        self.conn.commit()
    
    def search(self, query: str, top_k: int = 10) -> list[dict]:
        """
        Busca full-text com ranking por relevância.
        Retorna as páginas mais relevantes para a consulta.
        """
        # FTS5 com ranking BM25
        rows = self.conn.execute("""
            SELECT p.*, rank
            FROM pages_fts fts
            JOIN pages p ON p.id = fts.rowid
            WHERE pages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (self._prepare_fts_query(query), top_k)).fetchall()
        
        return [dict(row) for row in rows]
    
    def get_category_tree(self) -> dict:
        """Retorna a árvore de categorias com contagem de páginas."""
        rows = self.conn.execute("""
            SELECT category, subcategory, content_type, COUNT(*) as count
            FROM pages
            WHERE category != ''
            GROUP BY category, subcategory, content_type
            ORDER BY category, subcategory
        """).fetchall()
        
        tree = {}
        for row in rows:
            cat = row['category']
            if cat not in tree:
                tree[cat] = {'subcategories': {}, 'total': 0}
            tree[cat]['total'] += row['count']
            sub = row['subcategory']
            if sub:
                if sub not in tree[cat]['subcategories']:
                    tree[cat]['subcategories'][sub] = 0
                tree[cat]['subcategories'][sub] += row['count']
        
        return tree
    
    def get_page_with_context(self, url: str) -> dict | None:
        """
        Retorna uma página com todo seu contexto:
        links filhos, documentos, breadcrumb, páginas irmãs.
        """
        page = self.conn.execute(
            "SELECT * FROM pages WHERE url = ?", (url,)
        ).fetchone()
        
        if not page:
            return None
        
        result = dict(page)
        
        # Documentos associados
        result['documents'] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM documents WHERE page_url = ?", (url,)
        ).fetchall()]
        
        # Links filhos (páginas que esta página referencia)
        result['child_links'] = [dict(r) for r in self.conn.execute(
            "SELECT * FROM page_links WHERE source_url = ?", (url,)
        ).fetchall()]
        
        # Páginas irmãs (mesmo parent)
        if page['parent_url']:
            result['sibling_pages'] = [dict(r) for r in self.conn.execute("""
                SELECT url, title, content_type FROM pages 
                WHERE parent_url = ? AND url != ?
                ORDER BY title
            """, (page['parent_url'], url)).fetchall()]
        
        return result
    
    def _prepare_fts_query(self, query: str) -> str:
        """Prepara query para FTS5, tratando palavras-chave em português."""
        # Remover acentos e normalizar
        import unicodedata
        normalized = unicodedata.normalize('NFKD', query)
        ascii_text = normalized.encode('ascii', 'ignore').decode('ascii')
        
        # Tokenizar e preparar para FTS5
        words = ascii_text.lower().split()
        # Usar OR para busca mais ampla, com boost no título
        terms = ' OR '.join(words)
        return terms
    
    def export_to_json(self, output_path: str):
        """
        Exporta toda a base para JSON — formato alternativo ao SQLite
        para uso direto no prompt do LLM ou em ambientes sem SQLite.
        """
        pages = self.conn.execute("SELECT * FROM pages ORDER BY category, depth").fetchall()
        
        export = {
            'metadata': {
                'total_pages': len(pages),
                'exported_at': datetime.now().isoformat(),
                'base_url': 'https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas'
            },
            'categories': self.get_category_tree(),
            'pages': []
        }
        
        for page in pages:
            p = dict(page)
            p['documents'] = [dict(r) for r in self.conn.execute(
                "SELECT document_url, document_title, document_type FROM documents WHERE page_url = ?",
                (p['url'],)
            ).fetchall()]
            p['breadcrumb'] = json.loads(p.get('breadcrumb_json', '[]'))
            p['tags'] = json.loads(p.get('tags_json', '[]'))
            # Remover campos redundantes
            del p['breadcrumb_json']
            del p['tags_json']
            del p['search_text']
            export['pages'].append(p)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(export, f, ensure_ascii=False, indent=2)
        
        return output_path
```

---

## 3. Geração do Contexto para o LLM

### 3.1 Dois formatos de saída

O crawler gera **dois artefatos** complementares:

#### Artefato 1: `knowledge.db` (SQLite)
- Usado pelo **backend** para busca em tempo real
- Busca full-text com FTS5 (rápida, sem dependências externas)
- Permite consultas SQL complexas (árvore de navegação, documentos por categoria, etc.)
- ~5-10 MB para ~300 páginas

#### Artefato 2: `knowledge.json` (JSON exportado)
- Usado como **contexto direto** no prompt do LLM quando necessário
- Pode ser fatiado por categoria para enviar apenas o trecho relevante
- Formato legível, versionável no Git
- ~2-5 MB

#### Artefato 3: `knowledge.md` (Markdown — evolução do SKILL.md atual)
- Gerado a partir do JSON com formatação otimizada para LLMs
- Estruturado por seções temáticas com links diretos
- Inclui exemplos de consulta e instruções de uso
- Este é o formato que vai no **system prompt** do assistente

### 3.2 Pipeline de contexto no assistente

```
Pergunta do usuário
       │
       ▼
┌──────────────────────────┐
│  1. Busca FTS5 no SQLite │  → top 5 páginas relevantes
│     (busca por keywords) │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  2. Expandir contexto    │  → breadcrumb, docs, irmãs
│     (get_page_with_      │
│      context)            │
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  3. Montar prompt        │
│     - System prompt base │
│     - Páginas relevantes │  → máx 5 páginas com resumo
│     - Documentos listados│  → PDFs/CSVs com URL direta
│     - Links de navegação │  → links de subpáginas
└──────────┬───────────────┘
           │
           ▼
┌──────────────────────────┐
│  4. Gemini gera resposta │
│     com links e contexto │
└──────────────────────────┘
```

---

## 4. Script Orquestrador Completo

```python
#!/usr/bin/env python3
"""
crawler.py — Script de crawling e construção da base de conhecimento.

Uso:
    python crawler.py --full          # Crawling completo (primeira vez)
    python crawler.py --update        # Atualização incremental
    python crawler.py --export-json   # Exportar para JSON
    python crawler.py --export-md     # Exportar para Markdown (SKILL.md)
    python crawler.py --stats         # Estatísticas da base
"""
import argparse
import asyncio
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

BASE_URL = "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas"

async def full_crawl():
    """Pipeline completo: descoberta → extração → banco."""
    logger.info("=== FASE 1: Descoberta de URLs ===")
    urls = await discover_all_urls(BASE_URL)
    logger.info(f"URLs descobertas: {len(urls)}")
    
    logger.info("=== FASE 2: Extração de conteúdo ===")
    pages = await extract_all_pages(
        urls,
        concurrency=3,
        delay_between=0.5,
        progress_callback=lambda i, t, u: logger.info(f"[{i}/{t}] {u}")
    )
    logger.info(f"Páginas extraídas: {len(pages)}")
    
    logger.info("=== FASE 3: Construção do banco ===")
    db = KnowledgeDB("knowledge.db")
    for page in pages:
        db.insert_page(page)
    
    logger.info(f"Banco construído: knowledge.db")
    
    # Exportar também para JSON e MD
    db.export_to_json("knowledge.json")
    logger.info("Exportado: knowledge.json")
    
    export_to_markdown(db, "knowledge.md")
    logger.info("Exportado: knowledge.md")
    
    # Estatísticas
    print_stats(db)


async def incremental_update():
    """
    Atualização incremental: busca apenas páginas alteradas
    desde o último crawl (usando lastmod do sitemap).
    """
    db = KnowledgeDB("knowledge.db")
    
    # Pegar URLs do sitemap com lastmod
    sitemap_urls = await fetch_sitemap_urls(BASE_URL)
    
    updated_count = 0
    for url_data in sitemap_urls:
        url = url_data['url']
        lastmod = url_data.get('lastmod', '')
        
        # Verificar se a página mudou
        existing = db.conn.execute(
            "SELECT last_modified FROM pages WHERE url = ?", (url,)
        ).fetchone()
        
        if existing and existing['last_modified'] == lastmod:
            continue  # Não mudou, pular
        
        # Re-extrair esta página
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            page = await extract_page_data(url, client)
            db.insert_page(page)
            updated_count += 1
    
    logger.info(f"Páginas atualizadas: {updated_count}")
    
    # Re-exportar
    db.export_to_json("knowledge.json")
    export_to_markdown(db, "knowledge.md")


def print_stats(db: KnowledgeDB):
    """Imprime estatísticas da base."""
    stats = db.conn.execute("""
        SELECT 
            COUNT(*) as total,
            SUM(CASE WHEN content_type = 'page' THEN 1 ELSE 0 END) as pages,
            SUM(CASE WHEN content_type = 'pdf' THEN 1 ELSE 0 END) as pdfs,
            SUM(CASE WHEN content_type = 'csv' THEN 1 ELSE 0 END) as csvs,
            SUM(CASE WHEN content_type = 'video' THEN 1 ELSE 0 END) as videos,
            SUM(CASE WHEN content_type = 'error' THEN 1 ELSE 0 END) as errors,
            COUNT(DISTINCT category) as categories
        FROM pages
    """).fetchone()
    
    print(f"""
╔══════════════════════════════════════════╗
║   BASE DE CONHECIMENTO — ESTATÍSTICAS   ║
╠══════════════════════════════════════════╣
║ Total de entradas:  {stats['total']:>6}              ║
║ Páginas HTML:       {stats['pages']:>6}              ║
║ Documentos PDF:     {stats['pdfs']:>6}              ║
║ Planilhas CSV:      {stats['csvs']:>6}              ║
║ Vídeos:             {stats['videos']:>6}              ║
║ Erros:              {stats['errors']:>6}              ║
║ Categorias:         {stats['categories']:>6}              ║
╚══════════════════════════════════════════╝
    """)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true', help='Crawling completo')
    parser.add_argument('--update', action='store_true', help='Atualização incremental')
    parser.add_argument('--export-json', action='store_true')
    parser.add_argument('--export-md', action='store_true')
    parser.add_argument('--stats', action='store_true')
    args = parser.parse_args()
    
    if args.full:
        asyncio.run(full_crawl())
    elif args.update:
        asyncio.run(incremental_update())
    elif args.stats:
        print_stats(KnowledgeDB("knowledge.db"))
```

---

## 5. Agendamento e Manutenção

### 5.1 Frequência de atualização

| Tipo | Frequência | Comando |
|---|---|---|
| Crawling completo | Mensal ou após grandes atualizações do portal | `python crawler.py --full` |
| Atualização incremental | Semanal (via cron/CronJob no OpenShift) | `python crawler.py --update` |
| Exportação manual | Sob demanda | `python crawler.py --export-json` |

### 5.2 CronJob no OpenShift

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: transparencia-kb-update
spec:
  schedule: "0 3 * * 0"  # Todo domingo às 3h
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: crawler
            image: tre-pi-transparencia-chat:latest
            command: ["python", "crawler.py", "--update"]
            volumeMounts:
            - name: kb-data
              mountPath: /app/data
          restartPolicy: OnFailure
          volumes:
          - name: kb-data
            persistentVolumeClaim:
              claimName: transparencia-kb-pvc
```

---

## 6. Considerações Importantes

### 6.1 Ética e respeito ao servidor

- **Rate limiting:** Máximo 3 requests simultâneos com delay de 500ms
- **User-Agent identificado:** `TRE-PI-TransparenciaBot/1.0`
- **Respeitar robots.txt:** Verificar antes de iniciar
- **Horário:** Executar crawling em horários de baixa demanda (madrugada)
- **É nosso próprio servidor:** Mesmo assim, tratar com cuidado

### 6.2 Dados sensíveis

- O conteúdo é público (seção de transparência), então não há restrição de acesso
- Não armazenar cookies de sessão ou dados de autenticação
- Não acessar áreas autenticadas (SEI, portais internos)
- Filtrar URLs que redirecionam para domínios externos

### 6.3 Vantagem do Plone

O fato de ser Plone dá previsibilidade à estrutura HTML. Os seletores CSS são estáveis:
- `#content-core` → conteúdo editado
- `.documentFirstHeading` → título
- `.documentDescription` → descrição
- `#portal-breadcrumbs` → breadcrumb
- `.portalMessage` → mensagens do sistema

Isso torna o scraping muito mais confiável do que em sites com estrutura arbitrária.

### 6.4 Alternativa: API REST do Plone

Se o TRE-PI tiver o `plone.restapi` ativo, todo o crawling pode ser substituído por chamadas API:

```python
# Verificar se a API REST está disponível
# GET https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas
# Header: Accept: application/json
#
# Se retornar JSON ao invés de HTML, a API está ativa e podemos usar:
# /@search?portal_type=Document&path.query=/transparencia-e-prestacao-de-contas&b_size=100
# /@navigation?expand.navigation.depth=4
```

**Recomendação:** Antes de implementar o crawler completo, testar se a API REST do Plone responde. Se sim, usar como fonte primária — é mais estruturado, mais rápido e mais gentil com o servidor.
