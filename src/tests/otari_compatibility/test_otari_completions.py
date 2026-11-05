"""
/v1/chat/completions through MLPA, served by a real Otari.
"""

from mlpa.core.config import env
from tests.helpers import (
    CHAT_COMPLETIONS_PATH,
    MOCK_RESPONSE_TEXT,
    SERVICE_TYPE,
    chat_request,
    mlpa_headers,
    sse_content,
    wait_for_otari_spend,
)


class TestCompletionsThroughOtari:
    def test_completion_starts_an_end_user_on_its_service_types_budget(
        self, real_backend_client, otari
    ):
        """
        The headline check: MLPA's service key, its Otari-End-User-Budget
        header and its `user` field are all accepted, and Otari bills the
        request to an end user named `<identity>:<service type>`, started on
        that service type's budget, with spend recorded against it.
        """
        client, token, base_identity = real_backend_client

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["choices"][0]["message"]["content"]
        assert content == MOCK_RESPONSE_TEXT

        user = wait_for_otari_spend(otari, f"{base_identity}:{SERVICE_TYPE}")
        assert user["budget_id"] == env.service_type_config[SERVICE_TYPE]["budget_id"]

    def test_streaming_completion_is_served_and_billed(
        self, real_backend_client, otari
    ):
        """MLPA streams via a separate code path (client.stream in
        completions.py), and Otari only learns a stream's usage from the
        final chunk MLPA asks for with stream_options.include_usage, so
        both the content and the spend can break independently of the
        buffered path."""
        client, token, base_identity = real_backend_client

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(stream=True),
        )

        assert response.status_code == 200, response.text
        assert "text/event-stream" in response.headers["content-type"]
        # An upstream failure surfaces as an SSE error frame rather than a
        # non-200, so assert on the payload rather than the status alone.
        assert sse_content(response.text) == MOCK_RESPONSE_TEXT, response.text[:500]

        wait_for_otari_spend(otari, f"{base_identity}:{SERVICE_TYPE}")
