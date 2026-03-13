from pydantic import BaseModel, Field


class HistoryMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[HistoryMessage] = []


class LinkItem(BaseModel):
    title: str
    url: str
    type: str  # "page", "pdf", "csv", "video", "external"


class ChatResponse(BaseModel):
    text: str
    links: list[LinkItem] = []
    extracted_content: str | None = None
    suggestions: list[str] = []
    category: str | None = None


class SuggestResponse(BaseModel):
    suggestions: list[str]


class HealthResponse(BaseModel):
    status: str
    version: str = "1.0.0"
    knowledge_stats: dict = {}


class CategoriesResponse(BaseModel):
    categories: list[dict]
