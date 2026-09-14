"""
Gating and fixtures for the LiteLLM compatibility suite.

Skips the whole package locally when the docker-compose stack isn't up, but
fails loudly when MLPA_TEST_REQUIRE_REAL_BACKEND is set -- otherwise a
broken compose step in CI would silently "pass" the version gate by
skipping every test in it.
"""

import os
import uuid

import asyncpg
import httpx
import pytest
import redis

from mlpa.core.config import LITELLM_MASTER_AUTH_HEADERS, env
from tests.e2e.litellm_compat.helpers import FxAStub, real_backend_available

PROXY_TIMEOUT_S = 30.0

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
        item for item in items if "e2e/litellm_compat" in item.nodeid.replace("\\", "/")
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


@pytest.fixture
def proxy():
    """Client for talking to LiteLLM directly, pre-loaded with the master
    key. Only for reading back what the proxy recorded and for its admin
    endpoints -- anything MLPA can do itself belongs on real_backend_client
    so the test covers the integration rather than the proxy alone."""
    with httpx.Client(
        base_url=env.LITELLM_API_BASE,
        headers=LITELLM_MASTER_AUTH_HEADERS,
        timeout=PROXY_TIMEOUT_S,
    ) as client:
        yield client


@pytest.fixture
async def litellm_db():
    """Direct connection to LiteLLM's own Postgres database. MLPA writes
    into this schema with hand-written SQL (litellm_pg_service)"""
    conn = await asyncpg.connect(
        f"{env.PG_DB_URL.rstrip('/')}/{env.LITELLM_DB_NAME}",
        timeout=PROXY_TIMEOUT_S,
    )
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def redis_client():
    """LiteLLM's Redis, as exposed by litellm_docker_compose.yaml. MLPA
    doesn't talk to it, but its per-user budgets are only enforced across
    replicas"""
    client = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        decode_responses=True,
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def real_backend_client():
    """
    TestClient wired to the real LiteLLM proxy + Postgres, plus a bearer
    token/user_id pair unique to this test. Only FxA verification is
    mocked -- budget/rate-limit enforcement is a backend concern unrelated
    to auth, and this way the test doesn't depend on a live FxA account.

    Yields (client, token, base_identity).
    """
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from mlpa import run as main_app

    token = f"e2e-token-{uuid.uuid4().hex}"
    base_identity = f"e2e-{uuid.uuid4().hex[:12]}"
    fxa_stub = FxAStub(token, base_identity)
    with patch("mlpa.core.auth.fxa.client", fxa_stub):
        with TestClient(main_app.app) as client:
            yield client, token, base_identity
