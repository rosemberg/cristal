"""Testes unitários — docker-compose.yml e Containerfile (Etapa 0).

Verifica:
- Todas as variáveis de ambiente usam prefixo CRISTAL_
- Credenciais GCP montadas no path correto
- ADMIN_API_KEY presente
- Nenhuma variável sem prefixo (DATABASE_URL, VERTEX_PROJECT_ID etc.) presente
- depends_on com healthcheck configurado
"""

from __future__ import annotations

from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).parent.parent.parent
COMPOSE_FILE = PROJECT_ROOT / "docker-compose.yml"
CONTAINERFILE = PROJECT_ROOT / "Containerfile"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _load_compose() -> dict:  # type: ignore[type-arg]
    with COMPOSE_FILE.open() as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Testes docker-compose.yml
# ---------------------------------------------------------------------------


def test_compose_arquivo_existe() -> None:
    assert COMPOSE_FILE.exists(), "docker-compose.yml não encontrado"


def test_compose_app_usa_prefixo_cristal() -> None:
    """Todas as vars do serviço app devem ter prefixo CRISTAL_ ou ser vars shell."""
    data = _load_compose()
    env = data["services"]["app"]["environment"]
    env_keys = list(env.keys()) if isinstance(env, dict) else []

    # Vars que não precisam do prefixo (são injetadas pelo shell host)
    allowed_without_prefix = {"PORT", "GOOGLE_APPLICATION_CREDENTIALS"}

    for key in env_keys:
        if key in allowed_without_prefix:
            continue
        assert key.startswith("CRISTAL_"), (
            f"Variável '{key}' não usa prefixo CRISTAL_. "
            "Todas as vars do app devem usar CRISTAL_ para consistência com Settings."
        )


def test_compose_sem_variaveis_legadas() -> None:
    """Variáveis do formato legado (sem prefixo) não devem estar presentes."""
    data = _load_compose()
    env = data["services"]["app"]["environment"]
    env_keys = list(env.keys()) if isinstance(env, dict) else []

    variaveis_legadas = [
        "DATABASE_URL",
        "VERTEX_PROJECT_ID",
        "VERTEX_LOCATION",
        "VERTEX_MODEL",
        "ALLOWED_ORIGINS",
        "LOG_LEVEL",
        "ENVIRONMENT",
    ]
    for legacy in variaveis_legadas:
        assert legacy not in env_keys, (
            f"Variável legada '{legacy}' encontrada. Use 'CRISTAL_{legacy}' no lugar."
        )


def test_compose_database_url_usa_prefixo_cristal() -> None:
    """CRISTAL_DATABASE_URL deve estar definida no serviço app."""
    data = _load_compose()
    env = data["services"]["app"]["environment"]
    assert "CRISTAL_DATABASE_URL" in env, "CRISTAL_DATABASE_URL não encontrada no serviço app"


def test_compose_admin_api_key_presente() -> None:
    """CRISTAL_ADMIN_API_KEY deve estar configurada (com default para dev)."""
    data = _load_compose()
    env = data["services"]["app"]["environment"]
    assert "CRISTAL_ADMIN_API_KEY" in env, "CRISTAL_ADMIN_API_KEY não encontrada no serviço app"


def test_compose_db_healthcheck_configurado() -> None:
    """Serviço db deve ter healthcheck configurado."""
    data = _load_compose()
    db = data["services"]["db"]
    assert "healthcheck" in db, "Healthcheck não configurado no serviço db"
    hc = db["healthcheck"]
    assert "test" in hc
    assert "interval" in hc
    assert "retries" in hc


def test_compose_app_depends_on_db_healthcheck() -> None:
    """Serviço app deve depender do db com condição service_healthy."""
    data = _load_compose()
    depends = data["services"]["app"].get("depends_on", {})
    assert "db" in depends, "app não declara depends_on para db"
    condition = depends["db"].get("condition")
    assert condition == "service_healthy", (
        f"depends_on.db.condition deve ser 'service_healthy', foi: {condition!r}"
    )


def test_compose_credentials_montadas() -> None:
    """Credentials GCP devem ser montadas como volume no serviço app."""
    data = _load_compose()
    volumes = data["services"]["app"].get("volumes", [])
    # Pelo menos um volume deve apontar para /app/credentials.json
    credential_mounts = [v for v in volumes if "credentials.json" in str(v)]
    assert credential_mounts, "Nenhum volume montando credentials.json no serviço app"


def test_compose_volume_postgres_data_declarado() -> None:
    """Volume postgres_data deve estar declarado no nível raiz."""
    data = _load_compose()
    assert "postgres_data" in data.get("volumes", {}), (
        "Volume 'postgres_data' não declarado no nível raiz do docker-compose.yml"
    )


# ---------------------------------------------------------------------------
# Testes Containerfile
# ---------------------------------------------------------------------------


def test_containerfile_existe() -> None:
    assert CONTAINERFILE.exists(), "Containerfile não encontrado"


def test_containerfile_usa_entrypoint_nao_cmd() -> None:
    """Containerfile deve usar ENTRYPOINT (não CMD) para o entrypoint inteligente."""
    content = CONTAINERFILE.read_text()
    assert "ENTRYPOINT" in content, "Containerfile deve usar ENTRYPOINT"
    assert 'docker-entrypoint.sh' in content, "ENTRYPOINT deve apontar para docker-entrypoint.sh"


def test_containerfile_copia_alembic_e_migrations() -> None:
    """Containerfile deve copiar alembic.ini e migrations/ para o container."""
    content = CONTAINERFILE.read_text()
    assert "alembic.ini" in content, "alembic.ini não é copiado no Containerfile"
    assert "migrations/" in content, "migrations/ não é copiado no Containerfile"


def test_containerfile_copia_scripts() -> None:
    """Containerfile deve copiar scripts/ para o container."""
    content = CONTAINERFILE.read_text()
    assert "scripts/" in content, "scripts/ não é copiado no Containerfile"


def test_containerfile_usa_usuario_nao_root() -> None:
    """Containerfile deve criar e usar usuário não-root (UID 1001) para OpenShift."""
    content = CONTAINERFILE.read_text()
    assert "1001" in content, "Containerfile não configura UID 1001"
    assert "USER 1001" in content, "Containerfile não muda para USER 1001"


def test_containerfile_usa_prefixo_cristal_nas_envs() -> None:
    """Variáveis ENV no Containerfile devem usar prefixo CRISTAL_."""
    content = CONTAINERFILE.read_text()
    # Verificar que variáveis legadas não estão definidas
    assert "ENV VERTEX_PROJECT_ID" not in content, (
        "Containerfile define VERTEX_PROJECT_ID sem prefixo CRISTAL_"
    )
    assert "ENV VERTEX_LOCATION" not in content
    assert "ENV VERTEX_MODEL" not in content
    # Verificar presença das corretas
    assert "CRISTAL_VERTEX_PROJECT_ID" in content
    assert "CRISTAL_VERTEX_LOCATION" in content
    assert "CRISTAL_VERTEX_MODEL" in content
