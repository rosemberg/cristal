"""Integration test fixtures — PostgreSQL via testcontainers.

O container PostgreSQL e pg_settings são definidos em tests/conftest.py
e compartilhados com os testes E2E (um único container por sessão pytest).
"""

import docker
import pytest


def _docker_available() -> bool:
    try:
        docker.from_env().ping()
        return True
    except Exception:  # noqa: BLE001
        return False


docker_required = pytest.mark.skipif(
    not _docker_available(),
    reason="Docker daemon não está disponível — pule com: pytest -m 'not integration'",
)
