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
    vertex_model: str = "gemini-2.5-flash-lite"
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
