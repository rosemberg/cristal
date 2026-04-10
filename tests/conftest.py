"""Shared pytest fixtures for all test levels."""

import pytest


@pytest.fixture
def test_settings(monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    """Settings configurado para testes unitários (sem banco real)."""
    monkeypatch.setenv("CRISTAL_VERTEX_PROJECT_ID", "test-project")
    monkeypatch.setenv(
        "CRISTAL_DATABASE_URL",
        "postgresql+asyncpg://cristal:cristal@localhost:5432/cristal_test",
    )
    from importlib import reload

    import app.config.settings as mod

    reload(mod)
    from app.config.settings import Settings

    return Settings(_env_file=None)  # type: ignore[call-arg]
