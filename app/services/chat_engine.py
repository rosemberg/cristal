import json
import logging
import re

from app.models.schemas import ChatResponse, HistoryMessage, LinkItem
from app.prompts.system_prompt import build_system_prompt
from app.services.content_fetcher import ContentFetcher, FetchResult
from app.services.knowledge_base import KnowledgeBase, PageEntry
from app.services.vertex_client import VertexClient
from app.config import MAX_HISTORY_MESSAGES

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
    "pdf": "O documento está disponível em formato PDF. Clique no link abaixo para visualizar ou baixar. O PDF será aberto em uma nova aba do seu navegador.",
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


def _format_pages_for_prompt(pages: list[PageEntry]) -> str:
    if not pages:
        return "Nenhuma página específica identificada."
    lines = []
    for i, p in enumerate(pages, 1):
        lines.append(
            f"{i}. **{p.title}** ({p.category})\n"
            f"   URL: {p.url}\n"
            f"   Descrição: {p.description}\n"
            f"   Tipo: {p.content_type}"
        )
    return "\n\n".join(lines)


def _format_fetched_content(result: FetchResult | None, page: PageEntry | None) -> str:
    if result is None or page is None:
        return "Nenhum conteúdo extraído disponível."
    if result.is_media:
        instr = MEDIA_INSTRUCTIONS.get(result.content_type, "Acesse pelo link.")
        return f"[Conteúdo do tipo {result.content_type.upper()}] {instr}"
    if result.content_type == "error" or result.content is None:
        return f"Não foi possível extrair conteúdo da página '{page.title}'. Forneça o link direto."
    suffix = " [Conteúdo truncado — há mais informações na página original]" if result.truncated else ""
    return f"Página: {page.title}\n\n{result.content}{suffix}"


def _extract_json(text: str) -> dict:
    # Try direct JSON parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON block from markdown code fence
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find first { ... } block
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse JSON from LLM response: %.200s", text)
    return {}


def _build_response(raw: dict, pages: list[PageEntry]) -> ChatResponse:
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

    # Ensure at least links from top pages if LLM provided none
    if not links and pages:
        for p in pages[:3]:
            links.append(LinkItem(title=p.title, url=p.url, type=p.content_type))

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


def _should_fetch(message: str, page: PageEntry) -> bool:
    if page.content_type != "page":
        return False
    # Fetch if question seems to request actual content
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
        # 1. Search knowledge base
        relevant_pages = self.kb.search(message, top_k=5)
        logger.debug("Found %d relevant pages for query: %.100s", len(relevant_pages), message)

        # 2. Optionally fetch content from top page
        fetched: FetchResult | None = None
        top_page: PageEntry | None = None
        if relevant_pages:
            top_page = relevant_pages[0]
            if _should_fetch(message, top_page):
                try:
                    fetched = await self.fetcher.fetch_page_content(top_page.url)
                except Exception as exc:
                    logger.warning("Content fetch failed: %s", exc)

        # 3. Build system prompt
        pages_text = _format_pages_for_prompt(relevant_pages)
        fetched_text = _format_fetched_content(fetched, top_page)
        system_prompt = build_system_prompt(pages_text, fetched_text)

        # 4. Build messages for LLM (trim history)
        recent = history[-MAX_HISTORY_MESSAGES:]
        messages = [{"role": m.role, "content": m.content} for m in recent]
        messages.append({"role": "user", "content": message})

        # 5. Call Vertex AI
        try:
            raw_text = await self.vertex.generate(system_prompt, messages)
        except Exception as exc:
            logger.error("Vertex AI call failed: %s", exc)
            return FALLBACK_RESPONSE

        # 6. Parse and structure response
        raw_dict = _extract_json(raw_text)
        if not raw_dict:
            # Return plain text as fallback
            return ChatResponse(
                text=raw_text or FALLBACK_RESPONSE.text,
                links=[LinkItem(title=p.title, url=p.url, type=p.content_type) for p in relevant_pages[:3]],
                suggestions=INITIAL_SUGGESTIONS[:3],
            )

        return _build_response(raw_dict, relevant_pages)

    def get_initial_suggestions(self) -> list[str]:
        return INITIAL_SUGGESTIONS
