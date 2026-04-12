"""Application settings — Pydantic Settings v2.

Todas as variáveis de ambiente usam o prefixo CRISTAL_.
Exemplo: CRISTAL_VERTEX_PROJECT_ID, CRISTAL_DATABASE_URL.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuração centralizada e validada da aplicação Cristal."""

    # ── Banco de dados ────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://cristal:cristal@localhost:5432/cristal"
    db_pool_min: int = 2
    db_pool_max: int = 10

    # ── LLM / Vertex AI ──────────────────────────────────────────────────────
    vertex_project_id: str
    vertex_location: str = "us-central1"
    vertex_model: str = "gemini-3.1-flash-lite-preview"
    google_application_credentials: str = ""

    # ── Aplicação ─────────────────────────────────────────────────────────────
    environment: str = "development"
    allowed_origins: list[str] = ["*"]
    rate_limit_per_minute: int = 10
    log_level: str = "INFO"
    max_history_messages: int = 10
    max_content_length: int = 5000

    # ── Cache ─────────────────────────────────────────────────────────────────
    cache_ttl_seconds: int = 3600

    # ── Processamento de documentos ───────────────────────────────────────────
    chunk_size_tokens: int = 1000
    chunk_overlap_tokens: int = 200
    max_document_size_mb: int = 50

    # ── Embeddings ────────────────────────────────────────────────────────────
    vertex_embedding_model: str = "gemini-embedding-001"
    # gemini-embedding-001 exige região explícita (não suporta 'global')
    vertex_embedding_location: str = "us-central1"
    embedding_dimensions: int = 768        # output_dimensionality (schema DB = 768)
    embedding_cache_max_size: int = 256
    embedding_circuit_breaker_threshold: int = 3
    embedding_circuit_breaker_timeout: float = 60.0

    # ── Multi-Agent ──────────────────────────────────────────────────────────
    use_multi_agent: bool = False                                     # feature flag
    data_agent_model: str = "gemini-2.5-flash-preview-05-20"         # DataAgent (function calling)
    writer_agent_model: str = "gemini-2.5-flash-lite-preview-06-17"  # WriterAgent (leve)
    data_agent_temperature: float = 0.1
    writer_agent_temperature: float = 0.3
    data_agent_max_tool_rounds: int = 5

    # ── Circuit Breaker ───────────────────────────────────────────────────────
    circuit_breaker_threshold: int = 3
    circuit_breaker_timeout: float = 60.0
    data_agent_fallback_model: str = "gemini-2.5-flash-lite-preview-06-17"

    # ── SSE ───────────────────────────────────────────────────────────────────
    sse_enabled: bool = True
    sse_keepalive_seconds: int = 15

    # ── Admin ─────────────────────────────────────────────────────────────────
    admin_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="CRISTAL_",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


@lru_cache
def get_settings() -> Settings:
    """Retorna instância singleton das settings (carregada uma vez)."""
    return Settings()  # type: ignore[call-arg]
