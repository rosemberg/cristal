import json
import logging
import re

from app.config import MAX_CONTENT_LENGTH, MAX_HISTORY_MESSAGES
from app.models.schemas import ChatResponse, HistoryMessage, LinkItem
from app.prompts.system_prompt import build_system_prompt
from app.services.content_fetcher import ContentFetcher, FetchResult
from app.services.knowledge_base import KnowledgeBase
from app.services.vertex_client import VertexClient

logger = logging.getLogger(__name__)

INITIAL_SUGGESTIONS = [
    "Licitações em andamento",
    "Remuneração dos servidores",
    "Relatório de gestão",
    "LGPD e proteção de dados",
    "Contratos de TI",
    "Prestação de contas",
]

MEDIA_INSTRUCTIONS = {
    "pdf": "O documento está disponível em formato PDF. Clique no link abaixo para visualizar ou baixar.",
    "csv": "A planilha de dados está disponível para download. Você pode abrir o arquivo CSV no Excel ou Google Planilhas.",
    "video": "O vídeo está disponível no link abaixo. Ele será reproduzido em uma nova aba.",
    "api": "A API de dados está disponível no link abaixo com documentação Swagger interativa.",
}

FALLBACK_RESPONSE = ChatResponse(
    text="Desculpe, estou com dificuldades técnicas no momento. Por favor, tente novamente em alguns instantes ou acesse diretamente o portal de transparência do TRE-PI.",
    links=[
        LinkItem(
            title="Portal de Transparência do TRE-PI",
            url="https://www.tre-pi.jus.br/transparencia-e-prestacao-de-contas",
            type="page",
        )
    ],
    suggestions=INITIAL_SUGGESTIONS[:3],
    category=None,
)


def _format_pages_for_prompt(pages: list[dict]) -> str:
    """
    Formata as páginas encontradas na base de conhecimento para inclusão
    no prompt do sistema, incluindo documentos associados e breadcrumb.
    """
    if not pages:
        return "Nenhuma página específica identificada."

    sections = []
    for i, p in enumerate(pages, 1):
        lines = [f"### {i}. {p.get('title', 'Sem título')}"]
        lines.append(f"- **URL:** {p.get('url', '')}")

        cat = p.get("category", "")
        subcat = p.get("subcategory", "")
        if cat and subcat:
            lines.append(f"- **Categoria:** {cat} > {subcat}")
        elif cat:
            lines.append(f"- **Categoria:** {cat}")

        lines.append(f"- **Tipo:** {p.get('content_type', 'page')}")

        if p.get("description"):
            lines.append(f"- **Descrição:** {p['description']}")

        if p.get("content_summary"):
            lines.append(f"- **Resumo:** {p['content_summary']}")

        docs = p.get("documents", [])
        if docs:
            lines.append("- **Documentos associados:**")
            for doc in docs[:5]:
                dtype = doc.get("document_type", "pdf").upper()
                dtitle = doc.get("document_title") or "Documento"
                durl = doc.get("document_url", "")
                lines.append(f"  - {dtitle} -> {durl} (tipo: {dtype})")

        breadcrumb = p.get("breadcrumb", [])
        if breadcrumb:
            crumbs = " > ".join(b.get("title", "") for b in breadcrumb if b.get("title"))
            if crumbs:
                lines.append(f"- **Caminho de navegacao:** {crumbs}")

        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def _format_fetched_content(
    result: FetchResult | None, page: dict | None
) -> str:
    """
    Formata o conteúdo extraído de uma página para o prompt.
    Prioriza conteúdo já disponível no banco (main_content) antes
    de usar o resultado de uma requisição HTTP.
    """
    # Conteúdo direto do banco de dados — sem HTTP
    if page and page.get("main_content") and result is None:
        content = page["main_content"]
        truncated = len(content) > MAX_CONTENT_LENGTH
        if truncated:
            content = content[:MAX_CONTENT_LENGTH]
        suffix = " [Conteúdo truncado — há mais informações na página original]" if truncated else ""
        return f"Página: {page.get('title', '')}\n\n{content}{suffix}"

    if result is None:
        return "Nenhum conteúdo extraído disponível."

    if result.is_media:
        instr = MEDIA_INSTRUCTIONS.get(result.content_type, "Acesse pelo link.")
        return f"[Conteúdo do tipo {result.content_type.upper()}] {instr}"

    if result.content_type == "error" or result.content is None:
        title = page.get("title", "página") if page else "página"
        return f"Não foi possível extrair conteúdo da página '{title}'. Forneça o link direto."

    suffix = " [Conteúdo truncado — há mais informações na página original]" if result.truncated else ""
    title = page.get("title", "") if page else ""
    return f"Página: {title}\n\n{result.content}{suffix}"


def _extract_json(text: str) -> dict:
    """Extrai dict JSON do texto do LLM, com fallbacks para formatos malformados."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("Não foi possível parsear JSON da resposta do LLM: %.200s", text)
    return {}


def _build_response(raw: dict, pages: list[dict]) -> ChatResponse:
    """Constrói o ChatResponse a partir do dict retornado pelo LLM."""
    text = raw.get("text", "").strip()
    if not text:
        text = "Aqui estão as informações que encontrei sobre sua consulta."

    raw_links = raw.get("links", [])
    links: list[LinkItem] = []
    for lk in raw_links:
        if isinstance(lk, dict) and lk.get("url"):
            links.append(
                LinkItem(
                    title=lk.get("title", lk["url"]),
                    url=lk["url"],
                    type=lk.get("type", "page"),
                )
            )

    if not links and pages:
        for p in pages[:3]:
            links.append(
                LinkItem(
                    title=p.get("title", ""),
                    url=p.get("url", ""),
                    type=p.get("content_type", "page"),
                )
            )

    suggestions = [s for s in raw.get("suggestions", []) if isinstance(s, str)][:4]
    if not suggestions:
        suggestions = INITIAL_SUGGESTIONS[:3]

    extracted = raw.get("extracted_content") or None
    category = raw.get("category") or None

    return ChatResponse(
        text=text,
        links=links,
        extracted_content=extracted,
        suggestions=suggestions,
        category=category,
    )


def _should_fetch(message: str, page: dict) -> bool:
    """
    Determina se é necessário buscar conteúdo via HTTP para esta página.
    Retorna False se:
    - A página não é do tipo 'page' (pdf, csv, video, etc.)
    - O conteúdo já está disponível no banco (main_content preenchido)
    """
    if page.get("content_type", "page") != "page":
        return False
    if page.get("main_content"):
        return False
    fetch_triggers = [
        "o que", "quais", "como", "quando", "onde", "qual",
        "mostra", "lista", "detalhe", "informa", "explica",
        "conteúdo", "texto", "informação",
    ]
    msg_lower = message.lower()
    return any(t in msg_lower for t in fetch_triggers)


class ChatEngine:
    def __init__(
        self,
        kb: KnowledgeBase,
        fetcher: ContentFetcher,
        vertex: VertexClient,
    ) -> None:
        self.kb = kb
        self.fetcher = fetcher
        self.vertex = vertex

    async def process_message(
        self, message: str, history: list[HistoryMessage]
    ) -> ChatResponse:
        # 1. Buscar páginas relevantes na base de conhecimento
        relevant_pages = self.kb.search(message, top_k=5)
        logger.debug("Encontradas %d páginas para: %.100s", len(relevant_pages), message)

        # 2. Obter conteúdo da página principal
        fetched: FetchResult | None = None
        top_page: dict | None = None

        if relevant_pages:
            top_page = relevant_pages[0]
            content_type = top_page.get("content_type", "page")

            if content_type != "page":
                # PDF, CSV, vídeo etc. — não tentar buscar conteúdo
                pass
            elif top_page.get("main_content"):
                # Conteúdo já está no banco — usar diretamente, sem HTTP
                logger.debug("Usando main_content do banco para %s", top_page.get("url"))
            elif _should_fetch(message, top_page):
                # Conteúdo não está no banco — buscar via HTTP como fallback
                try:
                    fetched = await self.fetcher.fetch_page_content(top_page["url"])
                except Exception as exc:
                    logger.warning("Falha ao buscar conteúdo: %s", exc)

        # 3. Montar prompt com contexto rico
        pages_text = _format_pages_for_prompt(relevant_pages)
        fetched_text = _format_fetched_content(fetched, top_page)
        system_prompt = build_system_prompt(pages_text, fetched_text)

        # 4. Montar histórico recente para o LLM
        recent = history[-MAX_HISTORY_MESSAGES:]
        messages = [{"role": m.role, "content": m.content} for m in recent]
        messages.append({"role": "user", "content": message})

        # 5. Chamar Vertex AI
        try:
            raw_text = await self.vertex.generate(system_prompt, messages)
        except Exception as exc:
            logger.error("Falha na chamada ao Vertex AI: %s", exc)
            return FALLBACK_RESPONSE

        # 6. Parsear e estruturar resposta
        raw_dict = _extract_json(raw_text)
        if not raw_dict:
            return ChatResponse(
                text=raw_text or FALLBACK_RESPONSE.text,
                links=[
                    LinkItem(
                        title=p.get("title", ""),
                        url=p.get("url", ""),
                        type=p.get("content_type", "page"),
                    )
                    for p in relevant_pages[:3]
                ],
                suggestions=INITIAL_SUGGESTIONS[:3],
            )

        return _build_response(raw_dict, relevant_pages)

    def get_initial_suggestions(self) -> list[str]:
        """Retorna sugestões iniciais a partir das categorias da base de conhecimento."""
        try:
            return self.kb.get_suggestions()
        except Exception as exc:
            logger.warning("Erro ao obter sugestões do banco: %s", exc)
            return INITIAL_SUGGESTIONS
