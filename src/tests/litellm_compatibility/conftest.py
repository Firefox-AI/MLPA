"""
Gating and fixtures for the LiteLLM compatibility suite.

Skips the whole package locally when the docker-compose stack isn't up, but
fails loudly when MLPA_TEST_REQUIRE_REAL_BACKEND is set -- otherwise a
broken compose step in CI would silently "pass" the version gate by
skipping every test in it.
"""

import os

import pytest

from tests.fixtures import (  # noqa: F401
    litellm_db,
    proxy,
    real_backend_client,
    redis_client,
    use_real_get_or_create_user,
)
from tests.helpers import real_backend_available

_REQUIRE_REAL_BACKEND = os.environ.get(
    "MLPA_TEST_REQUIRE_REAL_BACKEND", ""
).lower() in {"1", "true", "yes"}

_SKIP_REASON = (
    "Requires a live LiteLLM proxy + Postgres + Redis. Run `make docker-up`, "
    "`bash scripts/migrate-app-attest-database-local.sh` and "
    "`uv run python scripts/create-and-set-virtual-key.py` first."
)


def pytest_collection_modifyitems(config, items):
    """Probe the backend once per session rather than per module, then either
    skip everything or abort the run."""
    litellm_items = [
        item
        for item in items
        if "litellm_compatibility" in item.nodeid.replace("\\", "/")
    ]
    if not litellm_items or real_backend_available():
        return

    if _REQUIRE_REAL_BACKEND:
        raise pytest.UsageError(
            "MLPA_TEST_REQUIRE_REAL_BACKEND is set but LiteLLM/Postgres are "
            "unreachable -- CI's docker-compose setup is broken."
        )

    skip_marker = pytest.mark.skip(reason=_SKIP_REASON)
    for item in litellm_items:
        item.add_marker(skip_marker)
