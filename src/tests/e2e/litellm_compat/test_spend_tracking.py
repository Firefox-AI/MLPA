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

import asyncio
import json
import time

from tests.helpers import (
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


class TestSpendLogsMetadata:
    """_build_litellm_body tags every completion with
    metadata.spend_logs_metadata = {purpose, country_code} for BigQuery
    reporting (mozdata.llm_proxy_litellm.spend_logs). Nothing else checks
    that LiteLLM actually persists this through to LiteLLM_SpendLogs."""

    async def test_purpose_and_country_land_in_spend_logs(
        self, real_backend_client, litellm_db
    ):
        client, token, base_identity = real_backend_client
        user_id = f"{base_identity}:{SERVICE_TYPE}"

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token, purpose="chat", **{"X-Geo-Country": "FR"}),
            json=chat_request(),
        )
        assert response.status_code == 200, response.text

        deadline = time.monotonic() + 30.0
        metadata = await litellm_db.fetchval(
            'SELECT metadata FROM "LiteLLM_SpendLogs" WHERE end_user = $1 '
            'ORDER BY "startTime" DESC LIMIT 1',
            user_id,
        )
        while time.monotonic() < deadline and metadata is None:
            await asyncio.sleep(1.0)
            metadata = await litellm_db.fetchval(
                'SELECT metadata FROM "LiteLLM_SpendLogs" WHERE end_user = $1 '
                'ORDER BY "startTime" DESC LIMIT 1',
                user_id,
            )
        assert metadata is not None, (
            f"no LiteLLM_SpendLogs row for {user_id!r} within 30s"
        )

        spend_logs_metadata = json.loads(metadata)["spend_logs_metadata"]
        assert spend_logs_metadata["purpose"] == "chat"
        assert spend_logs_metadata["country_code"] == "FR"
