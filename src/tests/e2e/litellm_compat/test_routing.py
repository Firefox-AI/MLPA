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

from mlpa.core.config import (
    ERROR_CODE_INVALID_MODEL_NAME,
    LITELLM_HEADER_RESPONSE_DURATION_MS,
    env,
)
from mlpa.core.litellm_routing import parse_litellm_routing_headers
from tests.e2e.litellm_compat.helpers import (
    CHAT_COMPLETIONS_PATH,
    MOCK_MODEL,
    MOCK_RESPONSE_TEXT,
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
