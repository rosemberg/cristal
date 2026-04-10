"""Tests for app/config/settings.py — Etapa 1 (TDD RED → GREEN)."""

import pytest
from pydantic import ValidationError


def test_settings_loads_with_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings deve carregar quando CRISTAL_VERTEX_PROJECT_ID está definido."""
    monkeypatch.setenv("CRISTAL_VERTEX_PROJECT_ID", "my-gcp-project")
    # Impede que .env sobrescreva as variáveis de ambiente do teste
    from importlib import reload

    import app.config.settings as mod

    reload(mod)
    from app.config.settings import get_settings

    s = get_settings()
    assert s.vertex_project_id == "my-gcp-project"


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Valores default devem estar corretos."""
    monkeypatch.setenv("CRISTAL_VERTEX_PROJECT_ID", "proj")
    from importlib import reload

    import app.config.settings as mod

    reload(mod)
    from app.config.settings import get_settings

    s = get_settings()
    assert s.vertex_location == "us-central1"
    assert s.vertex_model == "gemini-2.5-flash-lite"
    assert s.db_pool_min == 2
    assert s.db_pool_max == 10
    assert s.rate_limit_per_minute == 10
    assert s.max_history_messages == 10
    assert s.max_content_length == 5000
    assert s.cache_ttl_seconds == 3600
    assert s.chunk_size_tokens == 1000
    assert s.chunk_overlap_tokens == 200
    assert s.max_document_size_mb == 50
    assert s.google_application_credentials == ""


def test_settings_database_url_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """DATABASE_URL default deve apontar para postgres local."""
    monkeypatch.setenv("CRISTAL_VERTEX_PROJECT_ID", "proj")
    from importlib import reload

    import app.config.settings as mod

    reload(mod)
    from app.config.settings import get_settings

    s = get_settings()
    assert "postgresql" in s.database_url
    assert "5432" in s.database_url


def test_settings_vertex_project_id_required(monkeypatch: pytest.MonkeyPatch) -> None:
    """CRISTAL_VERTEX_PROJECT_ID é obrigatório — ValidationError se ausente."""
    monkeypatch.delenv("CRISTAL_VERTEX_PROJECT_ID", raising=False)
    from importlib import reload

    import app.config.settings as mod

    reload(mod)
    from app.config.settings import Settings

    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_settings_allowed_origins_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """allowed_origins deve aceitar lista via env var JSON."""
    monkeypatch.setenv("CRISTAL_VERTEX_PROJECT_ID", "proj")
    monkeypatch.setenv(
        "CRISTAL_ALLOWED_ORIGINS",
        '["https://www.tre-pi.jus.br", "https://transparencia.tre-pi.jus.br"]',
    )
    from importlib import reload

    import app.config.settings as mod

    reload(mod)
    from app.config.settings import get_settings

    s = get_settings()
    assert "https://www.tre-pi.jus.br" in s.allowed_origins


def test_settings_numeric_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """Variáveis numéricas devem ser parseadas corretamente."""
    monkeypatch.setenv("CRISTAL_VERTEX_PROJECT_ID", "proj")
    monkeypatch.setenv("CRISTAL_RATE_LIMIT_PER_MINUTE", "20")
    monkeypatch.setenv("CRISTAL_DB_POOL_MAX", "15")
    from importlib import reload

    import app.config.settings as mod

    reload(mod)
    from app.config.settings import get_settings

    s = get_settings()
    assert s.rate_limit_per_minute == 20
    assert s.db_pool_max == 15
