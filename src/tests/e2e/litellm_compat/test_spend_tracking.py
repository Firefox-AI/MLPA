"""
Spend accounting as MLPA depends on it, against a real proxy.

Every per-user budget MLPA enforces is settled against spend that LiteLLM
accumulates from the `user` field MLPA sends, and MLPA's own token metrics
come from the usage block LiteLLM returns. Both are the proxy's work, so
both are asserted here through MLPA's endpoint rather than against the
proxy directly.

test_budget_enforcement.py covers the enforcement side; this covers the
accounting it rests on.
"""

from tests.e2e.litellm_compat.helpers import (
    CHAT_COMPLETIONS_PATH,
    SERVICE_TYPE,
    chat_request,
    mlpa_headers,
    wait_for_spend,
)


class TestSpendTracking:
    def test_spend_accumulates_across_requests(self, real_backend_client, proxy):
        """A budget can only converge on its limit if each request adds to
        the running total. A version that overwrote spend rather than adding
        to it would leave budgets permanently unreachable."""
        client, token, base_identity = real_backend_client
        user_id = f"{base_identity}:{SERVICE_TYPE}"
        headers = mlpa_headers(token)

        first_response = client.post(
            CHAT_COMPLETIONS_PATH, headers=headers, json=chat_request()
        )
        assert first_response.status_code == 200, first_response.text
        first = wait_for_spend(proxy, user_id)["spend"]

        second_response = client.post(
            CHAT_COMPLETIONS_PATH, headers=headers, json=chat_request()
        )
        assert second_response.status_code == 200, second_response.text
        # Waits for a total strictly above the first reading, so the
        # already-flushed charge can't satisfy it on its own.
        second = wait_for_spend(proxy, user_id, above=first)["spend"]

        assert second > first, (
            f"Spend stayed at {first} across two completions -- LiteLLM is "
            "not accumulating spend per request."
        )

    def test_usage_is_reported_back_through_mlpa(self, real_backend_client):
        """MLPA reads prompt_tokens/completion_tokens off the response for
        its own metrics and logs a warning when they're absent (see
        _get_completion). An empty usage block would zero those out."""
        client, token, _ = real_backend_client

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(),
        )

        assert response.status_code == 200, response.text
        usage = response.json()["usage"]
        assert usage["prompt_tokens"] > 0
        assert usage["completion_tokens"] > 0
        assert usage["total_tokens"] == (
            usage["prompt_tokens"] + usage["completion_tokens"]
        )
