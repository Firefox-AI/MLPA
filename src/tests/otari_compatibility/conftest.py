"""
Gating and fixtures for the Otari compatibility suite.

Skips the whole package locally when MLPA isn't configured for Otari or the
docker-compose stack isn't up, but fails loudly when
MLPA_TEST_REQUIRE_REAL_BACKEND is set -- otherwise a broken compose step in CI
would silently "pass" by skipping every test in it.

GATEWAY_BACKEND and the OTARI_* settings are read once, when mlpa.core.config
is imported, so they have to be in the environment before pytest starts
rather than patched here.
"""

import os

import pytest

from mlpa.core.config import USE_OTARI
from tests.fixtures import otari, real_backend_client  # noqa: F401
from tests.helpers import otari_backend_available

_REQUIRE_REAL_BACKEND = os.environ.get(
    "MLPA_TEST_REQUIRE_REAL_BACKEND", ""
).lower() in {"1", "true", "yes"}

_SKIP_REASON = (
    "Requires a live Otari + Postgres and GATEWAY_BACKEND=otari. Run "
    "`docker compose -f otari_docker_compose.yaml up -d --build`, "
    "`POSTGRES_CONTAINER_NAME=otari_postgres DB_USERNAME=otari DB_PASSWORD=otari "
    "bash scripts/migrate-app-attest-database-local.sh`, then pytest with the "
    "env from .github/actions/otari-compatibility/action.yml."
)


def pytest_collection_modifyitems(config, items):
    """Probe the backend once per session rather than per module, then either
    skip everything or abort the run."""
    otari_items = [
        item
        for item in items
        if "otari_compatibility" in item.nodeid.replace("\\", "/")
    ]
    if not otari_items or (USE_OTARI and otari_backend_available()):
        return

    if _REQUIRE_REAL_BACKEND:
        raise pytest.UsageError(
            "MLPA_TEST_REQUIRE_REAL_BACKEND is set but GATEWAY_BACKEND is not "
            "otari, or Otari/Postgres are unreachable -- CI's docker-compose "
            "setup or the job's env is broken."
        )

    skip_marker = pytest.mark.skip(reason=_SKIP_REASON)
    for item in otari_items:
        item.add_marker(skip_marker)
