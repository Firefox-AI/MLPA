"""
Routing and error-mapping between MLPA and a real LiteLLM proxy.

Two things here can break MLPA without any MLPA code changing:

  * MLPA classifies upstream failures by matching on LiteLLM's error *text*
    (classify_upstream_error), so a reworded message silently turns a
    specific client error code into a generic 500.
  * MLPA reads x-litellm-* response headers for its routing telemetry, so a
    renamed header blanks that telemetry with nothing failing.

Both are asserted against the real proxy. test_mock_router_integration.py
in src/tests/integration mocks the router and cannot see either.
"""

import uuid

from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_INVALID_MODEL_NAME,
    LITELLM_HEADER_RESPONSE_DURATION_MS,
    env,
)
from mlpa.core.litellm_routing import parse_litellm_routing_headers
from tests.helpers import (
    BUDGET_TABLE,
    CHAT_COMPLETIONS_PATH,
    END_USER_TABLE,
    MOCK_MODEL,
    MOCK_RESPONSE_TEXT,
    SERVICE_TYPE,
    chat_request,
    mlpa_headers,
)


class TestRouting:
    def test_mlpa_routes_the_mock_model(self, real_backend_client):
        """The `mock` deployment points at a vertex_ai model but
        short-circuits via mock_response."""
        client, token, _ = real_backend_client

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["choices"][0]["message"]["content"] == MOCK_RESPONSE_TEXT
        assert body["model"] == MOCK_MODEL
        assert body["choices"][0]["finish_reason"] == "stop"

    def test_mlpa_maps_an_unknown_model_to_its_own_error_code(
        self, real_backend_client
    ):
        """MLPA recognises LiteLLM's "Invalid model name" 400 by matching its
        message text and translates it into error code 8. If the wording
        changes upstream, clients start getting an opaque 500 instead."""
        client, token, _ = real_backend_client

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(model="no-such-model-xyz"),
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"]["error"] == ERROR_CODE_INVALID_MODEL_NAME, (
            "MLPA did not classify LiteLLM's invalid-model error -- the "
            "upstream message wording likely changed."
        )

    def test_litellm_still_emits_the_routing_headers_mlpa_parses(self, proxy):
        """Sent as MLPA's own virtual key, since header emission can depend
        on the caller. Asserts through MLPA's parser rather than on raw
        header names, so this tracks whatever parse_litellm_routing_headers
        actually looks for."""
        response = proxy.post(
            CHAT_COMPLETIONS_PATH,
            json=chat_request(),
            headers={"Authorization": f"Bearer {env.MLPA_VIRTUAL_KEY}"},
        )
        assert response.status_code == 200, response.text

        assert LITELLM_HEADER_RESPONSE_DURATION_MS in response.headers, (
            f"LiteLLM no longer sends {LITELLM_HEADER_RESPONSE_DURATION_MS} "
            f"-- got {sorted(response.headers)}"
        )

        snapshot = parse_litellm_routing_headers(response.headers)
        assert snapshot.response_duration_ms is not None
        assert snapshot.attempted_fallbacks is not None
        assert snapshot.attempted_retries is not None


class TestBudgetErrorClassification:
    """Complements test_mlpa_maps_an_unknown_model_to_its_own_error_code
    above: same classify_upstream_error module, different error path
    (LiteLLM's per-user "ExceededBudget" text -> MLPA's error code 1).
    Seeded directly in Postgres, with spend already over budget, so
    there's no earlier zero-spend cache state to race against."""

    async def test_user_budget_exceeded_maps_to_error_code_1(
        self, real_backend_client, litellm_db
    ):
        client, token, base_identity = real_backend_client
        user_id = f"{base_identity}:{SERVICE_TYPE}"
        budget_id = f"e2e-exhausted-{uuid.uuid4().hex[:12]}"

        await litellm_db.execute(
            f"""
            INSERT INTO "{BUDGET_TABLE}"
            (budget_id, max_budget, budget_duration, created_at,
             updated_at, created_by, updated_by)
            VALUES ($1, $2, $3, NOW(), NOW(), $4, $4)
            """,
            budget_id,
            0.001,
            "1d",
            "e2e-test",
        )
        await litellm_db.execute(
            f"""
            INSERT INTO "{END_USER_TABLE}" (user_id, spend, budget_id, blocked)
            VALUES ($1, $2, $3, false)
            """,
            user_id,
            1.0,
            budget_id,
        )

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(),
        )
        assert response.status_code == 429, response.text
        assert response.json()["detail"]["error"] == ERROR_CODE_BUDGET_LIMIT_EXCEEDED
        assert response.headers.get("Retry-After") == "86400"


class TestRedisCache:
    def test_redis_cache_is_live(self, proxy):
        """Validate healthy redis startup"""
        response = proxy.get("/cache/ping")

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "healthy"
        assert body["cache_type"] == "redis"
        assert body["ping_response"] is True
        assert body["set_cache_response"] == "success"
