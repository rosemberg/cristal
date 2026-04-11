"""CLI adapter: Crawler do portal de transparência TRE-PI.

Refatorado para arquitetura hexagonal — persistência via porta PageRepository.

Fases:
  1. Descoberta de URLs (sitemap Plone + BFS recursivo)
  2. Extração de conteúdo HTML (título, descrição, breadcrumb, links, docs)
  3. Persistência idempotente via PageRepository (upsert)

Uso (módulo):
    python -m app.adapters.inbound.cli.crawler --full
    python -m app.adapters.inbound.cli.crawler --update
    python -m app.adapters.inbound.cli.crawler --stats
    python -m app.adapters.inbound.cli.crawler --test-url URL
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
import sys
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree

import httpx
from bs4 import BeautifulSoup

from app.domain.ports.outbound.page_repository import (
    CrawledDocument,
    CrawledLink,
    CrawledPage,
    PageRepository,
)

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# ─── Constantes ───────────────────────────────────────────────────────────────

BASE_URL = "https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas"
ALLOWED_PREFIX = "/transparencia-e-prestacao-de-contas"
TARGET_DOMAIN = "www.tre-pi.jus.br"
USER_AGENT = "TRE-PI-TransparenciaBot/1.0 (+transparencia-chat)"

_SLUG_MAP: dict[str, str] = {
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

_DOC_EXTENSIONS = frozenset({".pdf", ".csv", ".xlsx", ".xls"})
_NON_HTML_CONTENT_TYPES = frozenset({"pdf", "csv", "spreadsheet", "video", "google_sheet", "api"})


# ─── Value objects de configuração e resultado ────────────────────────────────


@dataclass
class CrawlerConfig:
    """Parâmetros de operação do crawler."""

    base_url: str = BASE_URL
    max_pages: int = 500
    max_depth: int = 7
    delay: float = 0.5
    concurrency: int = 3
    timeout: float = 15.0


@dataclass
class CrawlerStats:
    """Resultado de uma execução do crawler."""

    urls_discovered: int = 0
    pages_upserted: int = 0
    errors: int = 0
    duration_seconds: float = 0.0


# ─── Utilitários ──────────────────────────────────────────────────────────────


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _slug_to_title(slug: str) -> str:
    return _SLUG_MAP.get(slug, slug.replace("-", " ").replace("_", " ").title())


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


def _extract_candidate_links(html: str, base_url: str) -> list[str]:
    """Extrai links internos do domínio TRE-PI para descoberta BFS."""
    soup = BeautifulSoup(html, "html.parser")
    links: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        href = str(a_tag["href"]).strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)
        clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        if TARGET_DOMAIN in parsed.netloc:
            if ALLOWED_PREFIX in parsed.path:
                links.add(clean_url)
            elif "@@display-file" in parsed.path or parsed.path.endswith(
                tuple(_DOC_EXTENSIONS)
            ):
                links.add(full_url)
    return list(links)


def _build_http_client(timeout: float = 15.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,*/*",
        },
    )


# ─── Fase 1: Descoberta de URLs ───────────────────────────────────────────────


async def fetch_sitemap_urls(base_url: str) -> list[dict[str, object]]:
    """Obtém URLs da seção de transparência via sitemap Plone."""
    candidates = [
        f"{base_url}/sitemap.xml.gz",
        f"{base_url}/sitemap.xml",
        "https://www.tre-pi.jus.br/sitemap.xml.gz",
        "https://www.tre-pi.jus.br/sitemap.xml",
    ]
    all_urls: list[dict[str, object]] = []
    async with _build_http_client(timeout=30.0) as client:
        for sitemap_url in candidates:
            try:
                logger.info("  Tentando sitemap: %s", sitemap_url)
                resp = await client.get(sitemap_url)
                if resp.status_code != 200:
                    continue
                content = resp.content
                if sitemap_url.endswith(".gz"):
                    content = gzip.decompress(content)
                root = ElementTree.fromstring(content)  # noqa: S314
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                for url_elem in root.findall(".//sm:url", ns):
                    loc = url_elem.find("sm:loc", ns)
                    lastmod = url_elem.find("sm:lastmod", ns)
                    if loc is not None and loc.text and ALLOWED_PREFIX in loc.text:
                        all_urls.append(
                            {
                                "url": loc.text.strip(),
                                "lastmod": lastmod.text if lastmod is not None else None,
                                "discovered_via": "sitemap",
                            }
                        )
                logger.info("  Sitemap OK: %d URLs", len(all_urls))
                break
            except Exception as exc:  # noqa: BLE001
                logger.warning("  Sitemap falhou (%s): %s", sitemap_url, exc)
    return all_urls


async def crawl_discover_urls(
    start_url: str,
    max_pages: int = 500,
    max_depth: int = 7,
    delay: float = 0.5,
) -> dict[str, dict[str, object]]:
    """BFS a partir da raiz para descobrir URLs não listadas no sitemap."""
    visited: dict[str, dict[str, object]] = {}
    queue: deque[tuple[str, int, str | None]] = deque([(start_url, 0, None)])

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
                ct = head_resp.headers.get("content-type", "")
                visited[url] = {
                    "url": url,
                    "depth": depth,
                    "parent_url": parent,
                    "content_type": _classify_content_type(url, ct),
                    "status_code": head_resp.status_code,
                    "discovered_via": "crawl",
                }

                if "text/html" in ct:
                    await asyncio.sleep(delay)
                    resp = await client.get(url)
                    if resp.status_code == 200:
                        for link in _extract_candidate_links(resp.text, url):
                            if link not in visited:
                                queue.append((link, depth + 1, url))

                logger.debug("  [%d] %s", len(visited), url)

            except Exception as exc:  # noqa: BLE001
                visited[url] = {
                    "url": url,
                    "depth": depth,
                    "parent_url": parent,
                    "content_type": "error",
                    "error": str(exc),
                    "discovered_via": "crawl",
                }

    return visited


async def discover_all_urls(base_url: str) -> list[dict[str, object]]:
    """Combina sitemap + BFS para cobertura máxima, sem duplicatas."""
    logger.info("Fonte 1: Sitemap Plone")
    sitemap_urls = await fetch_sitemap_urls(base_url)

    logger.info("Fonte 2: Crawling recursivo BFS")
    crawled = await crawl_discover_urls(base_url)

    merged: dict[str, dict[str, object]] = {}

    for item in sitemap_urls:
        normalized = _normalize_url(str(item["url"]))
        merged[normalized] = {**item, "url": normalized}

    for url, data in crawled.items():
        normalized = _normalize_url(url)
        if normalized not in merged:
            merged[normalized] = data
        else:
            merged[normalized].update(
                {
                    k: v
                    for k, v in {
                        "depth": data.get("depth"),
                        "parent_url": data.get("parent_url"),
                        "content_type": data.get("content_type"),
                    }.items()
                    if v is not None
                }
            )

    result = list(merged.values())
    logger.info("Total de URLs descobertas (deduplicadas): %d", len(result))
    return result


# ─── Fase 2: Extração de conteúdo ─────────────────────────────────────────────


class PageExtractor:
    """Extrai dados estruturados de páginas HTML do portal Plone do TRE-PI."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def extract(self, url: str) -> CrawledPage:
        """Faz GET na URL e extrai metadados, conteúdo e links."""
        page = CrawledPage(url=url, title="")
        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                page.content_type = "error"
                page.description = f"HTTP {resp.status_code}"
                return page

            ct = resp.headers.get("content-type", "")
            if "text/html" not in ct:
                page.content_type = _classify_content_type(url, ct)
                return page

            soup = BeautifulSoup(resp.text, "html.parser")

            # 1. Título
            h1 = soup.find("h1", class_="documentFirstHeading")
            if h1:
                page.title = h1.get_text(strip=True)
            else:
                title_tag = soup.find("title")
                if title_tag:
                    page.title = title_tag.get_text(strip=True).split("—")[0].strip()

            # 2. Descrição
            desc_div = soup.find("div", class_="documentDescription")
            if desc_div:
                page.description = desc_div.get_text(strip=True)
            else:
                meta_desc = soup.find("meta", attrs={"name": "description"})
                if meta_desc:
                    page.description = str(meta_desc.get("content", ""))

            # 3. Breadcrumb
            bc_nav = soup.find("ol", id="portal-breadcrumbs") or soup.find(
                "nav", class_="breadcrumb"
            )
            if bc_nav:
                for li in bc_nav.find_all("li"):
                    a_tag = li.find("a")
                    if a_tag:
                        page.breadcrumb.append(
                            {
                                "title": a_tag.get_text(strip=True),
                                "url": urljoin(url, str(a_tag.get("href", ""))),
                            }
                        )
                    else:
                        text = li.get_text(strip=True)
                        if text:
                            page.breadcrumb.append({"title": text, "url": url})

            # 4. Conteúdo principal
            content_core = (
                soup.find("div", id="content-core")
                or soup.find("article")
                or soup.find("div", id="content")
            )
            if content_core:
                for tag in content_core.find_all(["script", "style", "nav", "footer"]):
                    tag.decompose()
                page.main_content = content_core.get_text(separator="\n", strip=True)
                if len(page.main_content) > 500:
                    page.content_summary = page.main_content[:500].rsplit(" ", 1)[0] + "..."
                else:
                    page.content_summary = page.main_content

                # 5. Links internos e documentos
                for a_tag in content_core.find_all("a", href=True):
                    href = urljoin(url, str(a_tag["href"]))
                    text = a_tag.get_text(strip=True)
                    if TARGET_DOMAIN in href and text:
                        is_doc = any(href.lower().endswith(ext) for ext in _DOC_EXTENSIONS)
                        is_doc = is_doc or "@@display-file" in href
                        if is_doc:
                            page.documents.append(
                                CrawledDocument(
                                    document_url=href,
                                    document_title=text,
                                    document_type=_classify_content_type(href, ""),
                                )
                            )
                        else:
                            page.internal_links.append(
                                CrawledLink(target_url=href, link_title=text)
                            )

            # 6. Categoria e subcategoria (derivadas da URL)
            path_parts = urlparse(url).path.split("/")
            tp_idx = next(
                (
                    i
                    for i, p in enumerate(path_parts)
                    if p == "transparencia-e-prestacao-de-contas"
                ),
                -1,
            )
            if tp_idx >= 0 and tp_idx + 1 < len(path_parts):
                page.category = _slug_to_title(path_parts[tp_idx + 1])
            if tp_idx >= 0 and tp_idx + 2 < len(path_parts):
                page.subcategory = _slug_to_title(path_parts[tp_idx + 2])

            # 7. Tags
            for meta_tag in soup.find_all("meta", attrs={"name": "keywords"}):
                keywords = str(meta_tag.get("content", ""))
                page.tags = [k.strip() for k in keywords.split(",") if k.strip()]

            # 8. Última modificação
            last_mod = soup.find("meta", attrs={"name": "DC.date.modified"})
            if last_mod:
                raw = str(last_mod.get("content", ""))
                if raw:
                    try:
                        page.last_modified = datetime.fromisoformat(raw)
                    except ValueError:
                        pass

        except Exception as exc:  # noqa: BLE001
            page.content_type = "error"
            page.description = f"Erro na extração: {exc}"

        return page


# ─── CrawlerCLI: orquestração ─────────────────────────────────────────────────


class CrawlerCLI:
    """Orquestra descoberta → extração → persistência idempotente.

    Exemplo de uso:
        repo = PostgresPageRepository(pool)
        crawler = CrawlerCLI(repo, CrawlerConfig())
        stats = await crawler.run_full()
    """

    def __init__(self, repo: PageRepository, config: CrawlerConfig | None = None) -> None:
        self._repo = repo
        self._config = config or CrawlerConfig()

    async def run_full(self) -> CrawlerStats:
        """Pipeline completo: descoberta → extração → upsert."""
        start = datetime.now()
        stats = CrawlerStats()

        logger.info("=== FASE 1: Descoberta de URLs ===")
        urls = await discover_all_urls(self._config.base_url)
        stats.urls_discovered = len(urls)
        logger.info("URLs descobertas: %d", stats.urls_discovered)

        logger.info("=== FASE 2: Extração + Persistência ===")
        await self._extract_and_upsert(urls, stats)

        stats.duration_seconds = (datetime.now() - start).total_seconds()
        logger.info(
            "Concluído: %d páginas upsertadas, %d erros (%.1fs)",
            stats.pages_upserted,
            stats.errors,
            stats.duration_seconds,
        )
        return stats

    async def run_update(self) -> CrawlerStats:
        """Atualização incremental via sitemap: re-extrai apenas páginas alteradas."""
        start = datetime.now()
        stats = CrawlerStats()

        logger.info("=== ATUALIZAÇÃO INCREMENTAL ===")
        sitemap_urls = await fetch_sitemap_urls(self._config.base_url)
        stats.urls_discovered = len(sitemap_urls)

        await self._extract_and_upsert(sitemap_urls, stats)

        stats.duration_seconds = (datetime.now() - start).total_seconds()
        logger.info(
            "Atualização concluída: %d/%d páginas processadas (%.1fs)",
            stats.pages_upserted,
            stats.urls_discovered,
            stats.duration_seconds,
        )
        return stats

    async def run_stats(self) -> dict[str, object]:
        """Retorna estatísticas do banco."""
        total = await self._repo.count_pages()
        return {"total_pages": total}

    async def _extract_and_upsert(
        self, url_list: list[dict[str, object]], stats: CrawlerStats
    ) -> None:
        """Extrai e persiste URLs com concorrência controlada."""
        semaphore = asyncio.Semaphore(self._config.concurrency)
        total = len(url_list)

        async with _build_http_client(self._config.timeout) as client:
            extractor = PageExtractor(client)

            async def process(idx: int, url_data: dict[str, object]) -> None:
                async with semaphore:
                    await asyncio.sleep(self._config.delay)
                    url = str(url_data["url"])
                    ct = str(url_data.get("content_type", "page"))

                    try:
                        if ct in _NON_HTML_CONTENT_TYPES:
                            page = CrawledPage(
                                url=url,
                                title=str(url_data.get("title", url.split("/")[-1])),
                                content_type=ct,
                                category=str(url_data.get("category", "")),
                                depth=int(url_data.get("depth") or 0),
                                parent_url=str(url_data.get("parent_url") or ""),
                            )
                        else:
                            page = await extractor.extract(url)
                            page.depth = int(url_data.get("depth") or 0)
                            page.parent_url = str(url_data.get("parent_url") or "")

                        await self._repo.upsert_page(page)
                        stats.pages_upserted += 1
                        logger.info("[%d/%d] OK: %s", idx + 1, total, url)

                    except Exception as exc:  # noqa: BLE001
                        stats.errors += 1
                        logger.error("[%d/%d] ERRO: %s — %s", idx + 1, total, url, exc)

            tasks = [process(i, u) for i, u in enumerate(url_list)]
            await asyncio.gather(*tasks)


# ─── CLI entry point ──────────────────────────────────────────────────────────


async def _test_url_cmd(url: str) -> None:
    """Testa a extração de uma única URL e exibe o resultado."""
    async with _build_http_client() as client:
        extractor = PageExtractor(client)
        page = await extractor.extract(url)

    print(f"\n{'='*60}")  # noqa: T201
    print(f"URL:        {page.url}")  # noqa: T201
    print(f"Tipo:       {page.content_type}")  # noqa: T201
    print(f"Título:     {page.title}")  # noqa: T201
    print(f"Categoria:  {page.category} / {page.subcategory}")  # noqa: T201
    print(f"Descrição:  {page.description[:120]}")  # noqa: T201
    print(f"Breadcrumb: {' > '.join(str(b['title']) for b in page.breadcrumb)}")  # noqa: T201
    print(f"Conteúdo:   {len(page.main_content)} chars")  # noqa: T201
    print(f"Links int.: {len(page.internal_links)}")  # noqa: T201
    print(f"Documentos: {len(page.documents)}")  # noqa: T201
    print(f"Tags:       {page.tags}")  # noqa: T201
    if page.documents:
        print("\nDocumentos encontrados:")  # noqa: T201
        for doc in page.documents[:10]:
            print(f"  [{doc.document_type}] {doc.document_title} — {doc.document_url}")  # noqa: T201
    print("=" * 60)  # noqa: T201


def main() -> None:
    """Entry point CLI — inicializa banco e delega ao CrawlerCLI."""
    import argparse

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="Crawler da base de conhecimento — Transparência TRE-PI"
    )
    parser.add_argument("--full", action="store_true", help="Crawling completo")
    parser.add_argument("--update", action="store_true", help="Atualização incremental")
    parser.add_argument("--stats", action="store_true", help="Estatísticas do banco")
    parser.add_argument("--test-url", metavar="URL", help="Testar extração de uma URL")
    args = parser.parse_args()

    if args.test_url:
        asyncio.run(_test_url_cmd(args.test_url))
        return

    if not (args.full or args.update or args.stats):
        parser.print_help()
        return

    # Importações pesadas apenas quando necessário
    from app.adapters.outbound.postgres.connection import DatabasePool, get_pool
    from app.adapters.outbound.postgres.page_repo import PostgresPageRepository
    from app.config.settings import get_settings

    settings = get_settings()

    async def _run() -> None:
        async with DatabasePool(settings) as db:
            pool = get_pool(db)
            repo = PostgresPageRepository(pool)
            crawler = CrawlerCLI(repo, CrawlerConfig())

            if args.full:
                stats = await crawler.run_full()
                result = json.dumps(
                    {
                        "urls_discovered": stats.urls_discovered,
                        "pages_upserted": stats.pages_upserted,
                        "errors": stats.errors,
                        "duration_seconds": round(stats.duration_seconds, 2),
                    },
                    indent=2,
                )
                print(result)  # noqa: T201
            elif args.update:
                stats = await crawler.run_update()
                result = json.dumps(
                    {
                        "urls_discovered": stats.urls_discovered,
                        "pages_upserted": stats.pages_upserted,
                        "errors": stats.errors,
                        "duration_seconds": round(stats.duration_seconds, 2),
                    },
                    indent=2,
                )
                print(result)  # noqa: T201
            elif args.stats:
                s = await crawler.run_stats()
                print(json.dumps(s, indent=2))  # noqa: T201

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        logger.info("Interrompido pelo usuário.")
        sys.exit(0)


if __name__ == "__main__":
    main()
