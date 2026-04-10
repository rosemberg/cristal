"""Integration test fixtures — PostgreSQL via testcontainers."""

import docker
import pytest
from testcontainers.postgres import PostgresContainer

from app.config.settings import Settings


def _docker_available() -> bool:
    """Verifica se o Docker daemon está acessível."""
    try:
        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001
        return False


docker_required = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon não está disponível — pule com: pytest -m 'not integration'",
)


@pytest.fixture(scope="session")
def postgres_container():  # type: ignore[no-untyped-def]
    """Sobe um PostgreSQL real via Docker para os testes de integração."""
    if not _docker_available():
        pytest.skip("Docker daemon não está disponível.")
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def pg_settings(postgres_container: PostgresContainer) -> Settings:
    """Settings apontando para o PostgreSQL do testcontainer."""
    url = postgres_container.get_connection_url()
    # Converte sqlalchemy URL para asyncpg: postgresql+psycopg2:// → postgresql+asyncpg://
    asyncpg_url = url.replace("postgresql+psycopg2://", "postgresql+asyncpg://").replace(
        "postgresql://", "postgresql+asyncpg://"
    )
    return Settings(
        vertex_project_id="test-project",
        database_url=asyncpg_url,
        db_pool_min=1,
        db_pool_max=3,
        _env_file=None,  # type: ignore[call-arg]
    )
