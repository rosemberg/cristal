#!/usr/bin/env python3
"""
crawler.py — Crawling e construção da base de conhecimento do TRE-PI.

Implementa a estratégia definida em ESTRATEGIA_CRAWLING_BASE_CONHECIMENTO.md:
  - Fase 1: Descoberta de URLs (sitemap + crawling recursivo BFS)
  - Fase 2: Extração de conteúdo HTML (título, descrição, breadcrumb, texto, links, documentos)
  - Fase 3: Construção do banco (SQLite com FTS5) + exportação (JSON + Markdown)

Uso:
    python scripts/crawler.py --full          # Crawling completo (primeira vez)
    python scripts/crawler.py --update        # Atualização incremental
    python scripts/crawler.py --export-json   # Exportar knowledge.json
    python scripts/crawler.py --export-md     # Exportar knowledge.md
    python scripts/crawler.py --stats         # Estatísticas da base
    python scripts/crawler.py --test-url URL  # Testar extração de uma URL
"""

import argparse
import asyncio
import gzip
import json
import logging
import sqlite3
import sys
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

BASE_URL = "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas"
ALLOWED_PREFIX = "/transparencia-e-prestacao-de-contas"
TARGET_DOMAIN = "www.tre-pi.jus.br"
USER_AGENT = "TRE-PI-TransparenciaBot/1.0 (+transparencia-chat)"

DATA_DIR = Path(__file__).parent.parent / "app" / "data"
DB_PATH = DATA_DIR / "knowledge.db"
JSON_PATH = DATA_DIR / "knowledge.json"
MD_PATH = DATA_DIR / "knowledge.md"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema SQL
# ---------------------------------------------------------------------------

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT DEFAULT '',
    main_content TEXT DEFAULT '',
    content_summary TEXT DEFAULT '',
    category TEXT DEFAULT '',
    subcategory TEXT DEFAULT '',
    content_type TEXT DEFAULT 'page',
    depth INTEGER DEFAULT 0,
    parent_url TEXT DEFAULT '',
    breadcrumb_json TEXT DEFAULT '[]',
    tags_json TEXT DEFAULT '[]',
    last_modified TEXT DEFAULT '',
    extracted_at TEXT DEFAULT '',
    search_text TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS page_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_url TEXT NOT NULL,
    target_url TEXT NOT NULL,
    link_title TEXT DEFAULT '',
    link_type TEXT DEFAULT 'internal',
    FOREIGN KEY (source_url) REFERENCES pages(url)
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    page_url TEXT NOT NULL,
    document_url TEXT NOT NULL,
    document_title TEXT DEFAULT '',
    document_type TEXT DEFAULT 'pdf',
    context TEXT DEFAULT '',
    FOREIGN KEY (page_url) REFERENCES pages(url)
);

CREATE TABLE IF NOT EXISTS navigation_tree (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    parent_url TEXT NOT NULL,
    child_url TEXT NOT NULL,
    child_title TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0,
    UNIQUE(parent_url, child_url)
);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
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

CREATE INDEX IF NOT EXISTS idx_pages_category ON pages(category);
CREATE INDEX IF NOT EXISTS idx_pages_content_type ON pages(content_type);
CREATE INDEX IF NOT EXISTS idx_pages_depth ON pages(depth);
CREATE INDEX IF NOT EXISTS idx_page_links_source ON page_links(source_url);
CREATE INDEX IF NOT EXISTS idx_page_links_target ON page_links(target_url);
CREATE INDEX IF NOT EXISTS idx_documents_page ON documents(page_url);
CREATE INDEX IF NOT EXISTS idx_nav_parent ON navigation_tree(parent_url);
"""

SLUG_MAP = {
    "gestao-de-pessoas": "Gestão de Pessoas",
    "licitacoes-e-contratos": "Licitações, Contratos e Instrumentos de Cooperação",
    "governanca": "Gestão e Governança",
    "gestao-orcamentaria-e-financeira": "Gestão Orçamentária e Financeira",
    "planos-de-auditoria-interna": "Auditoria",
    "audiencias-publicas": "Audiências e Sessões",
    "colegiados": "Colegiados",
    "contabilidade": "Contabilidade",
    "estatistica-processual": "Estatística Processual",
    "gestao-patrimonial-e-infraestrutura": "Gestão Patrimonial e Infraestrutura",
    "lei-de-acesso-a-informacao-declaracao-anual": "Lei de Acesso à Informação",
    "lgpd-lei-geral-de-protecao-de-dados": "LGPD - Proteção de Dados",
    "ouvidoria": "Ouvidoria",
    "prestacao-de-constas-da-gestao": "Prestação de Contas da Gestão",
    "sei": "SEI - Sistema Eletrônico de Informações",
    "servico-de-informacoes-ao-cidadao-sic": "Serviço de Informação ao Cidadão (SIC)",
    "sustentabililidade_acessibilidade_inclusao": "Sustentabilidade, Acessibilidade e Inclusão",
    "tecnologia-da-informacao-e-comunicacao-1": "Tecnologia da Informação",
    "transparencia-cnj": "Transparência CNJ",
    "relatorios-cnj": "Relatórios CNJ",
    "relatorios-tre-pi": "Relatórios TRE-PI",
}


# ---------------------------------------------------------------------------
# Modelos de dados
# ---------------------------------------------------------------------------

@dataclass
class PageData:
    url: str
    title: str = ""
    description: str = ""
    breadcrumb: list[dict] = field(default_factory=list)
    main_content: str = ""
    content_summary: str = ""
    internal_links: list[dict] = field(default_factory=list)
    documents: list[dict] = field(default_factory=list)
    category: str = ""
    subcategory: str = ""
    content_type: str = "page"
    depth: int = 0
    parent_url: str = ""
    tags: list[str] = field(default_factory=list)
    last_modified: str = ""
    extracted_at: str = ""


# ---------------------------------------------------------------------------
# Utilitários
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _slug_to_title(slug: str) -> str:
    return SLUG_MAP.get(slug, slug.replace("-", " ").replace("_", " ").title())


def _classify_content_type(url: str, http_content_type: str) -> str:
    url_lower = url.lower()
    if url_lower.endswith(".pdf") or "@@display-file" in url_lower:
        return "pdf"
    if url_lower.endswith(".csv"):
        return "csv"
    if url_lower.endswith((".xlsx", ".xls")):
        return "spreadsheet"
    if "youtube.com" in url_lower or "youtu.be" in url_lower:
        return "video"
    if "docs.google.com/spreadsheets" in url_lower:
        return "google_sheet"
    if "swagger" in url_lower:
        return "api"
    if "text/html" in http_content_type:
        return "page"
    if "application/pdf" in http_content_type:
        return "pdf"
    return "unknown"


def _extract_links(html: str, base_url: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = set()
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if TARGET_DOMAIN in parsed.netloc:
            if ALLOWED_PREFIX in parsed.path:
                links.add(clean_url)
            elif "@@display-file" in parsed.path or parsed.path.endswith((".pdf", ".csv", ".xlsx")):
                links.add(full_url)
    return list(links)


def _build_http_client(timeout: float = 15, **kwargs) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*",
        },
        **kwargs,
    )


# ---------------------------------------------------------------------------
# FASE 1 — Descoberta de URLs
# ---------------------------------------------------------------------------

async def fetch_sitemap_urls(base_url: str) -> list[dict]:
    """Obtém todas as URLs da seção de transparência via sitemap do Plone."""
    sitemap_candidates = [
        f"{base_url}/sitemap.xml.gz",
        f"{base_url}/sitemap.xml",
        "https://www.tre-pi.jus.br/sitemap.xml.gz",
        "https://www.tre-pi.jus.br/sitemap.xml",
    ]
    all_urls = []
    async with _build_http_client(timeout=30) as client:
        for sitemap_url in sitemap_candidates:
            try:
                logger.info(f"  Tentando sitemap: {sitemap_url}")
                resp = await client.get(sitemap_url)
                if resp.status_code != 200:
                    continue
                content = resp.content
                if sitemap_url.endswith(".gz"):
                    content = gzip.decompress(content)
                root = ElementTree.fromstring(content)
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                for url_elem in root.findall(".//sm:url", ns):
                    loc = url_elem.find("sm:loc", ns)
                    lastmod = url_elem.find("sm:lastmod", ns)
                    if loc is not None:
                        url = loc.text.strip()
                        if ALLOWED_PREFIX in url:
                            all_urls.append({
                                "url": url,
                                "lastmod": lastmod.text if lastmod is not None else None,
                                "discovered_via": "sitemap",
                            })
                logger.info(f"  Sitemap OK: {len(all_urls)} URLs encontradas")
                break
            except Exception as exc:
                logger.warning(f"  Sitemap falhou ({sitemap_url}): {exc}")
    return all_urls


async def crawl_discover_urls(
    start_url: str,
    max_pages: int = 500,
    max_depth: int = 7,
    delay: float = 0.5,
) -> dict[str, dict]:
    """BFS a partir da raiz para descobrir URLs não listadas no sitemap."""
    visited: dict[str, dict] = {}
    queue: deque = deque([(start_url, 0, None)])

    async with _build_http_client() as client:
        while queue and len(visited) < max_pages:
            url, depth, parent = queue.popleft()

            if url in visited or depth > max_depth:
                continue

            parsed = urlparse(url)
            if parsed.netloc not in ("", TARGET_DOMAIN):
                continue
            if ALLOWED_PREFIX not in parsed.path:
                continue

            try:
                head_resp = await client.head(url)
                content_type = head_resp.headers.get("content-type", "")
                visited[url] = {
                    "url": url,
                    "depth": depth,
                    "parent_url": parent,
                    "content_type": _classify_content_type(url, content_type),
                    "status_code": head_resp.status_code,
                    "discovered_via": "crawl",
                }

                if "text/html" in content_type:
                    await asyncio.sleep(delay)
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        for link in _extract_links(resp.text, url):
                            if link not in visited:
                                queue.append((link, depth + 1, url))

                logger.debug(f"  [{len(visited)}] {url}")

            except Exception as exc:
                visited[url] = {
                    "url": url,
                    "depth": depth,
                    "parent_url": parent,
                    "content_type": "error",
                    "error": str(exc),
                    "discovered_via": "crawl",
                }

    return visited


async def discover_all_urls(base_url: str) -> list[dict]:
    """Combina sitemap + crawling BFS para cobertura máxima."""
    logger.info("Fonte 1: Sitemap Plone")
    sitemap_urls = await fetch_sitemap_urls(base_url)

    logger.info("Fonte 2: Crawling recursivo BFS")
    crawled = await crawl_discover_urls(base_url)

    all_urls: dict[str, dict] = {}

    for item in sitemap_urls:
        url = _normalize_url(item["url"])
        all_urls[url] = {**item, "url": url}

    for url, data in crawled.items():
        normalized = _normalize_url(url)
        if normalized not in all_urls:
            all_urls[normalized] = data
        else:
            all_urls[normalized].update({
                "depth": data.get("depth"),
                "parent_url": data.get("parent_url"),
                "content_type": data.get("content_type", all_urls[normalized].get("content_type")),
            })

    result = list(all_urls.values())
    logger.info(f"Total de URLs descobertas (deduplicadas): {len(result)}")
    return result


# ---------------------------------------------------------------------------
# FASE 2 — Extração de conteúdo
# ---------------------------------------------------------------------------

async def extract_page_data(url: str, client: httpx.AsyncClient) -> PageData:
    """Extrai dados estruturados de uma página HTML do portal Plone do TRE-PI."""
    page = PageData(url=url, extracted_at=datetime.now().isoformat())

    try:
        resp = await client.get(url)
        if resp.status_code != 200:
            page.content_type = "error"
            page.description = f"HTTP {resp.status_code}"
            return page

        content_type = resp.headers.get("content-type", "")
        if "text/html" not in content_type:
            page.content_type = _classify_content_type(url, content_type)
            return page

        soup = BeautifulSoup(resp.text, "html.parser")

        # 1. TÍTULO
        h1 = soup.find("h1", class_="documentFirstHeading")
        if h1:
            page.title = h1.get_text(strip=True)
        else:
            title_tag = soup.find("title")
            if title_tag:
                page.title = title_tag.get_text(strip=True).split("—")[0].strip()

        # 2. DESCRIÇÃO
        desc_div = soup.find("div", class_="documentDescription")
        if desc_div:
            page.description = desc_div.get_text(strip=True)
        else:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc:
                page.description = meta_desc.get("content", "")

        # 3. BREADCRUMB
        breadcrumb_nav = soup.find("ol", id="portal-breadcrumbs") or soup.find(
            "nav", class_="breadcrumb"
        )
        if breadcrumb_nav:
            for li in breadcrumb_nav.find_all("li"):
                a_tag = li.find("a")
                if a_tag:
                    page.breadcrumb.append({
                        "title": a_tag.get_text(strip=True),
                        "url": urljoin(url, a_tag.get("href", "")),
                    })
                else:
                    text = li.get_text(strip=True)
                    if text:
                        page.breadcrumb.append({"title": text, "url": url})

        # 4. CONTEÚDO PRINCIPAL
        content_core = soup.find("div", id="content-core")
        if not content_core:
            content_core = soup.find("article") or soup.find("div", id="content")

        if content_core:
            for tag in content_core.find_all(["script", "style", "nav", "footer"]):
                tag.decompose()
            page.main_content = content_core.get_text(separator="\n", strip=True)
            if len(page.main_content) > 500:
                page.content_summary = page.main_content[:500].rsplit(" ", 1)[0] + "..."
            else:
                page.content_summary = page.main_content

        # 5. LINKS INTERNOS E DOCUMENTOS
        if content_core:
            for a_tag in content_core.find_all("a", href=True):
                href = urljoin(url, a_tag["href"])
                text = a_tag.get_text(strip=True)
                if TARGET_DOMAIN in href and text:
                    is_doc = any(href.lower().endswith(ext) for ext in (".pdf", ".csv", ".xlsx"))
                    is_doc = is_doc or "@@display-file" in href
                    entry = {"title": text, "url": href}
                    if is_doc:
                        entry["type"] = _classify_content_type(href, "")
                        page.documents.append(entry)
                    else:
                        page.internal_links.append(entry)

        # 6. CATEGORIA e SUBCATEGORIA (derivadas da URL)
        path_parts = urlparse(url).path.split("/")
        tp_index = next(
            (i for i, p in enumerate(path_parts) if p == "transparencia-e-prestacao-de-contas"),
            -1,
        )
        if tp_index >= 0 and tp_index + 1 < len(path_parts):
            page.category = _slug_to_title(path_parts[tp_index + 1])
        if tp_index >= 0 and tp_index + 2 < len(path_parts):
            page.subcategory = _slug_to_title(path_parts[tp_index + 2])

        # 7. TAGS
        for meta_tag in soup.find_all("meta", attrs={"name": "keywords"}):
            keywords = meta_tag.get("content", "")
            page.tags = [k.strip() for k in keywords.split(",") if k.strip()]

        # 8. ÚLTIMA MODIFICAÇÃO
        last_mod = soup.find("meta", attrs={"name": "DC.date.modified"})
        if last_mod:
            page.last_modified = last_mod.get("content", "")

    except Exception as exc:
        page.content_type = "error"
        page.description = f"Erro na extração: {exc}"

    return page


async def extract_all_pages(
    urls: list[dict],
    concurrency: int = 3,
    delay_between: float = 0.5,
    progress_callback=None,
) -> list[PageData]:
    """Extrai conteúdo de todas as URLs com rate limiting responsável."""
    semaphore = asyncio.Semaphore(concurrency)
    total = len(urls)

    async with _build_http_client() as client:

        async def process_url(idx: int, url_data: dict) -> PageData:
            async with semaphore:
                await asyncio.sleep(delay_between)
                url = url_data["url"]
                ct = url_data.get("content_type", "page")

                if ct in ("pdf", "csv", "spreadsheet", "video", "google_sheet", "api"):
                    page = PageData(
                        url=url,
                        content_type=ct,
                        title=url_data.get("title", url.split("/")[-1]),
                        category=url_data.get("category", ""),
                        extracted_at=datetime.now().isoformat(),
                    )
                else:
                    page = await extract_page_data(url, client)

                page.depth = url_data.get("depth") or 0
                page.parent_url = url_data.get("parent_url") or ""

                if progress_callback:
                    progress_callback(idx + 1, total, url)

                return page

        tasks = [process_url(i, u) for i, u in enumerate(urls)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    return [r for r in results if isinstance(r, PageData)]


# ---------------------------------------------------------------------------
# FASE 3 — Base de Conhecimento
# ---------------------------------------------------------------------------

class KnowledgeDB:
    def __init__(self, db_path: str | Path = DB_PATH):
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self):
        self.conn.executescript(SCHEMA_SQL)
        self.conn.commit()

    def insert_page(self, page: PageData):
        search_text = (
            f"{page.title} {page.description} {page.main_content} {' '.join(page.tags)}"
        ).lower()

        self.conn.execute(
            """
            INSERT OR REPLACE INTO pages
            (url, title, description, main_content, content_summary,
             category, subcategory, content_type, depth, parent_url,
             breadcrumb_json, tags_json, last_modified, extracted_at, search_text)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
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
                search_text,
            ),
        )

        for link in page.internal_links:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO page_links (source_url, target_url, link_title, link_type)
                VALUES (?, ?, ?, 'internal')
                """,
                (page.url, link["url"], link["title"]),
            )

        for doc in page.documents:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO documents (page_url, document_url, document_title, document_type)
                VALUES (?, ?, ?, ?)
                """,
                (page.url, doc["url"], doc["title"], doc.get("type", "pdf")),
            )

        if page.parent_url:
            self.conn.execute(
                """
                INSERT OR IGNORE INTO navigation_tree (parent_url, child_url, child_title)
                VALUES (?, ?, ?)
                """,
                (page.parent_url, page.url, page.title),
            )

        self.conn.commit()

    def search(self, query: str, top_k: int = 10) -> list[dict]:
        fts_query = self._prepare_fts_query(query)
        rows = self.conn.execute(
            """
            SELECT p.*, rank
            FROM pages_fts fts
            JOIN pages p ON p.id = fts.rowid
            WHERE pages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, top_k),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_category_tree(self) -> dict:
        rows = self.conn.execute(
            """
            SELECT category, subcategory, content_type, COUNT(*) as count
            FROM pages
            WHERE category != ''
            GROUP BY category, subcategory, content_type
            ORDER BY category, subcategory
            """
        ).fetchall()
        tree: dict = {}
        for row in rows:
            cat = row["category"]
            if cat not in tree:
                tree[cat] = {"subcategories": {}, "total": 0}
            tree[cat]["total"] += row["count"]
            sub = row["subcategory"]
            if sub:
                tree[cat]["subcategories"].setdefault(sub, 0)
                tree[cat]["subcategories"][sub] += row["count"]
        return tree

    def get_page_with_context(self, url: str) -> dict | None:
        page = self.conn.execute("SELECT * FROM pages WHERE url = ?", (url,)).fetchone()
        if not page:
            return None
        result = dict(page)
        result["documents"] = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM documents WHERE page_url = ?", (url,)
            ).fetchall()
        ]
        result["child_links"] = [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM page_links WHERE source_url = ?", (url,)
            ).fetchall()
        ]
        if page["parent_url"]:
            result["sibling_pages"] = [
                dict(r)
                for r in self.conn.execute(
                    """
                    SELECT url, title, content_type FROM pages
                    WHERE parent_url = ? AND url != ?
                    ORDER BY title
                    """,
                    (page["parent_url"], url),
                ).fetchall()
            ]
        return result

    def get_stats(self) -> dict:
        row = self.conn.execute(
            """
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN content_type = 'page' THEN 1 ELSE 0 END) as pages,
                SUM(CASE WHEN content_type = 'pdf' THEN 1 ELSE 0 END) as pdfs,
                SUM(CASE WHEN content_type = 'csv' THEN 1 ELSE 0 END) as csvs,
                SUM(CASE WHEN content_type = 'spreadsheet' THEN 1 ELSE 0 END) as spreadsheets,
                SUM(CASE WHEN content_type = 'video' THEN 1 ELSE 0 END) as videos,
                SUM(CASE WHEN content_type = 'error' THEN 1 ELSE 0 END) as errors,
                COUNT(DISTINCT category) as categories
            FROM pages
            """
        ).fetchone()
        return dict(row)

    def _prepare_fts_query(self, query: str) -> str:
        normalized = unicodedata.normalize("NFKD", query)
        ascii_text = normalized.encode("ascii", "ignore").decode("ascii")
        words = [w for w in ascii_text.lower().split() if len(w) > 2]
        return " OR ".join(words) if words else ascii_text

    def export_to_json(self, output_path: str | Path = JSON_PATH) -> str:
        pages = self.conn.execute(
            "SELECT * FROM pages ORDER BY category, depth"
        ).fetchall()

        export = {
            "metadata": {
                "total_pages": len(pages),
                "exported_at": datetime.now().isoformat(),
                "base_url": BASE_URL,
            },
            "categories": self.get_category_tree(),
            "pages": [],
        }

        for page in pages:
            p = dict(page)
            p["documents"] = [
                dict(r)
                for r in self.conn.execute(
                    "SELECT document_url, document_title, document_type FROM documents WHERE page_url = ?",
                    (p["url"],),
                ).fetchall()
            ]
            p["breadcrumb"] = json.loads(p.pop("breadcrumb_json", "[]"))
            p["tags"] = json.loads(p.pop("tags_json", "[]"))
            p.pop("search_text", None)
            export["pages"].append(p)

        output_path = Path(output_path)
        output_path.write_text(
            json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return str(output_path)

    def export_to_markdown(self, output_path: str | Path = MD_PATH) -> str:
        """Exporta a base para Markdown otimizado para uso como system prompt do LLM."""
        tree = self.get_category_tree()
        pages_by_cat: dict[str, list[dict]] = {}

        rows = self.conn.execute(
            """
            SELECT url, title, description, content_summary, content_type,
                   category, subcategory, depth, breadcrumb_json
            FROM pages
            WHERE content_type != 'error'
            ORDER BY category, depth, title
            """
        ).fetchall()

        for row in rows:
            p = dict(row)
            cat = p["category"] or "Geral"
            pages_by_cat.setdefault(cat, []).append(p)

        lines = [
            "# Base de Conhecimento — Transparência TRE-PI",
            f"\n_Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}_\n",
            f"**URL base:** {BASE_URL}\n",
            "---\n",
            "## Estrutura Geral\n",
        ]

        for cat, info in sorted(tree.items()):
            lines.append(f"- **{cat}** ({info['total']} itens)")
            for sub in sorted(info["subcategories"]):
                lines.append(f"  - {sub}")

        lines.append("\n---\n")

        for cat, cat_pages in sorted(pages_by_cat.items()):
            lines.append(f"## {cat}\n")
            for p in cat_pages:
                indent = "  " * max(0, (p["depth"] or 1) - 1)
                if p["content_type"] == "page":
                    lines.append(f"{indent}### [{p['title'] or p['url']}]({p['url']})")
                    if p["description"]:
                        lines.append(f"{indent}{p['description']}\n")
                    if p["content_summary"]:
                        summary = p["content_summary"][:300]
                        lines.append(f"{indent}> {summary}\n")
                else:
                    icon = {"pdf": "📄", "csv": "📊", "video": "🎥", "api": "🔌"}.get(
                        p["content_type"], "📎"
                    )
                    lines.append(
                        f"{indent}- {icon} [{p['title'] or p['url']}]({p['url']}) _{p['content_type'].upper()}_"
                    )

            # Documentos da categoria
            doc_rows = self.conn.execute(
                """
                SELECT d.document_url, d.document_title, d.document_type
                FROM documents d
                JOIN pages p ON p.url = d.page_url
                WHERE p.category = ?
                ORDER BY d.document_type, d.document_title
                """,
                (cat,),
            ).fetchall()
            if doc_rows:
                lines.append(f"\n**Documentos em {cat}:**\n")
                for doc in doc_rows:
                    icon = {"pdf": "📄", "csv": "📊"}.get(doc["document_type"], "📎")
                    lines.append(
                        f"- {icon} [{doc['document_title'] or doc['document_url']}]({doc['document_url']})"
                    )

            lines.append("")

        output_path = Path(output_path)
        output_path.write_text("\n".join(lines), encoding="utf-8")
        return str(output_path)

    def close(self):
        self.conn.close()


# ---------------------------------------------------------------------------
# Orquestrador
# ---------------------------------------------------------------------------

async def full_crawl():
    """Pipeline completo: descoberta → extração → banco."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("=== FASE 1: Descoberta de URLs ===")
    urls = await discover_all_urls(BASE_URL)
    logger.info(f"URLs descobertas: {len(urls)}")

    logger.info("=== FASE 2: Extração de conteúdo ===")

    def progress(i, t, u):
        logger.info(f"[{i:>3}/{t}] {u}")

    pages = await extract_all_pages(
        urls,
        concurrency=3,
        delay_between=0.5,
        progress_callback=progress,
    )
    logger.info(f"Páginas extraídas: {len(pages)}")

    logger.info("=== FASE 3: Construção do banco ===")
    db = KnowledgeDB(DB_PATH)
    for page in pages:
        db.insert_page(page)
    logger.info(f"Banco construído: {DB_PATH}")

    path_json = db.export_to_json(JSON_PATH)
    logger.info(f"Exportado: {path_json}")

    path_md = db.export_to_markdown(MD_PATH)
    logger.info(f"Exportado: {path_md}")

    print_stats(db)
    db.close()


async def incremental_update():
    """Atualização incremental: re-extrai apenas páginas com lastmod diferente."""
    if not DB_PATH.exists():
        logger.error("knowledge.db não encontrado. Execute --full primeiro.")
        sys.exit(1)

    db = KnowledgeDB(DB_PATH)
    sitemap_urls = await fetch_sitemap_urls(BASE_URL)
    updated_count = 0

    async with _build_http_client() as client:
        for url_data in sitemap_urls:
            url = url_data["url"]
            lastmod = url_data.get("lastmod", "")
            existing = db.conn.execute(
                "SELECT last_modified FROM pages WHERE url = ?", (url,)
            ).fetchone()

            if existing and existing["last_modified"] == lastmod:
                continue

            logger.info(f"Atualizando: {url}")
            page = await extract_page_data(url, client)
            db.insert_page(page)
            updated_count += 1
            await asyncio.sleep(0.5)

    logger.info(f"Páginas atualizadas: {updated_count}")

    if updated_count > 0:
        db.export_to_json(JSON_PATH)
        db.export_to_markdown(MD_PATH)
        logger.info("Re-exportado: knowledge.json e knowledge.md")

    db.close()


async def test_url(url: str):
    """Testa a extração de uma única URL e exibe o resultado."""
    async with _build_http_client() as client:
        page = await extract_page_data(url, client)
    print(f"\n{'='*60}")
    print(f"URL:        {page.url}")
    print(f"Tipo:       {page.content_type}")
    print(f"Título:     {page.title}")
    print(f"Categoria:  {page.category} / {page.subcategory}")
    print(f"Descrição:  {page.description[:120]}")
    print(f"Breadcrumb: {' > '.join(b['title'] for b in page.breadcrumb)}")
    print(f"Conteúdo:   {len(page.main_content)} chars")
    print(f"Links int.: {len(page.internal_links)}")
    print(f"Documentos: {len(page.documents)}")
    print(f"Tags:       {page.tags}")
    if page.documents:
        print("\nDocumentos encontrados:")
        for doc in page.documents[:10]:
            print(f"  [{doc.get('type','?')}] {doc['title']} — {doc['url']}")
    print("="*60)


def print_stats(db: KnowledgeDB):
    stats = db.get_stats()
    print(f"""
╔══════════════════════════════════════════╗
║   BASE DE CONHECIMENTO — ESTATÍSTICAS   ║
╠══════════════════════════════════════════╣
║ Total de entradas:  {stats['total']:>6}              ║
║ Páginas HTML:       {stats['pages']:>6}              ║
║ Documentos PDF:     {stats['pdfs']:>6}              ║
║ Planilhas CSV:      {stats['csvs']:>6}              ║
║ Planilhas Excel:    {stats['spreadsheets']:>6}              ║
║ Vídeos:             {stats['videos']:>6}              ║
║ Erros:              {stats['errors']:>6}              ║
║ Categorias:         {stats['categories']:>6}              ║
╚══════════════════════════════════════════╝
""")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Crawler da base de conhecimento — Transparência TRE-PI"
    )
    parser.add_argument("--full", action="store_true", help="Crawling completo (primeira vez)")
    parser.add_argument("--update", action="store_true", help="Atualização incremental")
    parser.add_argument("--export-json", action="store_true", help="Exportar para JSON")
    parser.add_argument("--export-md", action="store_true", help="Exportar para Markdown")
    parser.add_argument("--stats", action="store_true", help="Estatísticas da base")
    parser.add_argument("--test-url", metavar="URL", help="Testar extração de uma URL")
    args = parser.parse_args()

    if args.full:
        asyncio.run(full_crawl())
    elif args.update:
        asyncio.run(incremental_update())
    elif args.export_json:
        db = KnowledgeDB(DB_PATH)
        path = db.export_to_json(JSON_PATH)
        logger.info(f"Exportado: {path}")
        db.close()
    elif args.export_md:
        db = KnowledgeDB(DB_PATH)
        path = db.export_to_markdown(MD_PATH)
        logger.info(f"Exportado: {path}")
        db.close()
    elif args.stats:
        db = KnowledgeDB(DB_PATH)
        print_stats(db)
        db.close()
    elif args.test_url:
        asyncio.run(test_url(args.test_url))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
