"""
Budget enforcement tests for /v1/chat/completions, against real services.

Unlike the rest of src/tests/integration, these hit a real LiteLLM proxy +
Postgres instead of the mocked litellm_pg/get_completion fixtures. Requires
`make docker-up` and a migrated app_attest DB. Skips locally if either is
unreachable; CI sets MLPA_TEST_REQUIRE_REAL_BACKEND=true so the same
situation fails the build instead.
"""

import asyncio
import os
import time
import uuid

import asyncpg
import httpx
import pytest
from fastapi.testclient import TestClient

from mlpa.core.config import LITELLM_MASTER_AUTH_HEADERS, env


def _db_reachable(db_name: str) -> bool:
    async def _connect() -> bool:
        try:
            conn = await asyncpg.connect(
                f"{env.PG_DB_URL.rstrip('/')}/{db_name}", timeout=2.0
            )
            await conn.close()
            return True
        except Exception:
            return False

    return asyncio.run(_connect())


def _real_backend_available() -> bool:
    try:
        httpx.get(
            f"{env.LITELLM_API_BASE}/health/liveliness", timeout=2.0
        ).raise_for_status()
    except Exception:
        return False
    return _db_reachable(env.LITELLM_DB_NAME) and _db_reachable(env.APP_ATTEST_DB_NAME)


_BACKEND_AVAILABLE = _real_backend_available()
_REQUIRE_REAL_BACKEND = os.environ.get(
    "MLPA_TEST_REQUIRE_REAL_BACKEND", ""
).lower() in {
    "1",
    "true",
    "yes",
}

if not _BACKEND_AVAILABLE and _REQUIRE_REAL_BACKEND:
    # CI sets MLPA_TEST_REQUIRE_REAL_BACKEND so a broken docker-compose step
    # fails the build loudly instead of silently skipping these tests.
    pytest.fail(
        "MLPA_TEST_REQUIRE_REAL_BACKEND is set but LiteLLM/Postgres are "
        "unreachable -- CI's docker-compose setup is broken.",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _BACKEND_AVAILABLE,
    reason=(
        "Requires a live LiteLLM proxy + Postgres. Run `make docker-up` "
        "and `uv run alembic upgrade head` first."
    ),
)

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"


class _FxAStub:
    """Maps a single bearer token to a single FxA base identity, so each
    test can get a fresh, collision-free user_id against the real LiteLLM
    end-user table."""

    def __init__(self, token: str, base_identity: str):
        self._token = token
        self._base_identity = base_identity

    def verify_token(
        self, token: str, scope: str = "profile:uid", include_verification_source=False
    ):
        if token == self._token:
            result = {"user": self._base_identity}
            if include_verification_source:
                result["verification_source"] = "local"
            return result
        raise Exception("Invalid token")


def _chat_request(**overrides) -> dict:
    body = {
        "model": "mock",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    body.update(overrides)
    return body


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

    from mlpa import run as main_app

    token = f"itest-token-{uuid.uuid4().hex}"
    base_identity = f"itest-{uuid.uuid4().hex[:12]}"
    fxa_stub = _FxAStub(token, base_identity)
    with patch("mlpa.core.auth.fxa.client", fxa_stub):
        with TestClient(main_app.app) as client:
            yield client, token, base_identity


def _customer_info(user_id: str) -> dict | None:
    """Ask LiteLLM itself whether it knows about this end user."""
    response = httpx.get(
        f"{env.LITELLM_API_BASE}/customer/info",
        params={"end_user_id": user_id},
        headers=LITELLM_MASTER_AUTH_HEADERS,
        timeout=5.0,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def _wait_for_spend(user_id: str, *, timeout_s: float = 30.0) -> dict:
    """LiteLLM writes spend logs via a batched background worker (~15s in
    local docker-compose), so poll for it instead of asserting immediately
    after the request returns."""
    deadline = time.monotonic() + timeout_s
    info = _customer_info(user_id)
    while time.monotonic() < deadline:
        if info is not None and info["spend"] > 0:
            return info
        time.sleep(1.0)
        info = _customer_info(user_id)
    assert info is not None and info["spend"] > 0, (
        f"LiteLLM recorded no spend for end user {user_id!r} within "
        f"{timeout_s}s of a successful completion -- the `user` field "
        "likely isn't reaching LiteLLM."
    )
    return info


class TestChatCompletionBudgetEnforcement:
    def test_chat_completion_registers_end_user_with_budget_in_litellm(
        self, real_backend_client
    ):
        """
        A successful completion must make LiteLLM aware of the end user under
        the memories budget -- i.e. the `user` field actually reached
        LiteLLM. This is the outcome check that a body-shape unit test alone
        (like the one #243 broke) can't provide.
        """
        client, token, base_identity = real_backend_client
        user_id = f"{base_identity}:memories"

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers={
                "authorization": f"Bearer {token}",
                "service-type": "memories",
                "purpose": "memory-generation",
            },
            json=_chat_request(),
        )

        assert response.status_code == 200

        info = _wait_for_spend(user_id)
        assert (
            info["litellm_budget_table"]["budget_id"]
            == env.user_feature_budget["memories"]["budget_id"]
        )

    def test_chat_completion_enforces_rpm_budget(self, real_backend_client):
        """
        Drives one end user past its configured RPM limit and asserts LiteLLM
        actually rejects it (429, error code 2). This is a behavioral check:
        it doesn't care *how* the budget is enforced, only that it is --
        so it would have caught #243 regardless of which field was dropped.
        """
        client, token, _base_identity = real_backend_client
        rpm_limit = env.user_feature_budget["memories"]["rpm_limit"]
        headers = {
            "authorization": f"Bearer {token}",
            "service-type": "memories",
            "purpose": "memory-generation",
        }

        rejected = None
        for _ in range(rpm_limit + 5):
            response = client.post(
                CHAT_COMPLETIONS_PATH, headers=headers, json=_chat_request()
            )
            if response.status_code == 429:
                rejected = response
                break
            assert response.status_code == 200, response.text

        assert rejected is not None, (
            f"Sent {rpm_limit + 5} requests for a single user with an "
            f"{rpm_limit}-RPM budget and none were rate-limited -- budget "
            "enforcement is not working."
        )
        assert rejected.json()["detail"]["error"] == 2
        assert rejected.headers.get("Retry-After") == "60"
