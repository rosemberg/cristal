import re
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class PageEntry:
    title: str
    url: str
    description: str
    category: str
    keywords: list[str]
    content_type: str  # "page", "pdf", "csv", "video", "audio", "api", "external"


@dataclass
class KnowledgeBase:
    pages: list[PageEntry] = field(default_factory=list)
    categories: dict[str, list[PageEntry]] = field(default_factory=dict)

    def search(self, query: str, top_k: int = 5) -> list[PageEntry]:
        query_lower = query.lower()
        query_words = set(re.findall(r"\w+", query_lower))

        scored: list[tuple[float, PageEntry]] = []
        for page in self.pages:
            score = 0.0

            # Check title match (highest weight)
            title_words = set(re.findall(r"\w+", page.title.lower()))
            title_overlap = query_words & title_words
            score += len(title_overlap) * 3.0

            # Check keyword match (high weight)
            for kw in page.keywords:
                kw_words = set(re.findall(r"\w+", kw.lower()))
                if kw_words & query_words:
                    score += 2.0
                # Partial substring match
                if kw.lower() in query_lower or query_lower in kw.lower():
                    score += 1.5

            # Check description match (medium weight)
            desc_words = set(re.findall(r"\w+", page.description.lower()))
            desc_overlap = query_words & desc_words
            score += len(desc_overlap) * 1.0

            # Check category match (low weight)
            cat_words = set(re.findall(r"\w+", page.category.lower()))
            cat_overlap = query_words & cat_words
            score += len(cat_overlap) * 0.5

            if score > 0:
                scored.append((score, page))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [page for _, page in scored[:top_k]]

    def get_by_category(self, category: str) -> list[PageEntry]:
        return self.categories.get(category, [])


def _detect_content_type(url: str) -> str:
    url_lower = url.lower()
    if url_lower.endswith(".pdf"):
        return "pdf"
    if url_lower.endswith(".csv"):
        return "csv"
    if "youtube" in url_lower or "transmissao-ao-vivo" in url_lower or "videos" in url_lower:
        return "video"
    if "swagger" in url_lower or "/api" in url_lower:
        return "api"
    return "page"


def _derive_category(url: str) -> str:
    segments = url.rstrip("/").split("/")
    known = {
        "licitacoes-e-contratos": "Licitações e Contratos",
        "gestao-de-pessoas": "Gestão de Pessoas",
        "orcamento-e-financas": "Orçamento e Finanças",
        "prestacao-de-contas": "Prestação de Contas",
        "planos-de-auditoria-interna": "Auditoria",
        "governanca": "Gestão e Governança",
        "lgpd": "LGPD",
        "tecnologia-da-informacao": "Tecnologia da Informação",
        "sessoes": "Sessões e Plenário",
        "dados-abertos": "Dados Abertos",
        "acesso-a-informacao": "Acesso à Informação",
        "responsabilidade-socioambiental": "Responsabilidade Socioambiental",
    }
    for seg in segments:
        if seg in known:
            return known[seg]
    return "Geral"


def _parse_knowledge_md(path: Path) -> KnowledgeBase:
    kb = KnowledgeBase()
    content = path.read_text(encoding="utf-8")

    # Split into sections by ### headings
    sections = re.split(r"^### ", content, flags=re.MULTILINE)

    for section in sections[1:]:  # skip intro
        lines = section.strip().split("\n")
        if not lines:
            continue

        title = lines[0].strip()
        url = ""
        description = ""
        keywords: list[str] = []
        content_type = "page"
        category = "Geral"

        for line in lines[1:]:
            line = line.strip()
            if line.startswith("- **URL:**"):
                url = line.replace("- **URL:**", "").strip()
            elif line.startswith("- **Descrição:**"):
                description = line.replace("- **Descrição:**", "").strip()
            elif line.startswith("- **Palavras-chave:**"):
                kw_raw = line.replace("- **Palavras-chave:**", "").strip()
                keywords = [k.strip() for k in kw_raw.split(",") if k.strip()]
            elif line.startswith("- **Tipo:**"):
                content_type = line.replace("- **Tipo:**", "").strip()

        if not url:
            continue

        category = _derive_category(url)
        if content_type == "page":
            content_type = _detect_content_type(url)

        entry = PageEntry(
            title=title,
            url=url,
            description=description,
            category=category,
            keywords=keywords,
            content_type=content_type,
        )
        kb.pages.append(entry)
        kb.categories.setdefault(category, []).append(entry)

    logger.info("KnowledgeBase loaded: %d pages in %d categories", len(kb.pages), len(kb.categories))
    return kb


def load_knowledge_base(path: str) -> KnowledgeBase:
    p = Path(path)
    if not p.exists():
        logger.warning("Knowledge base file not found: %s", path)
        return KnowledgeBase()
    return _parse_knowledge_md(p)
