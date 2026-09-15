"""
MLPA -> LiteLLM authentication, against a real proxy.
"""

from unittest.mock import patch

from mlpa.core.config import env
from tests.helpers import (
    CHAT_COMPLETIONS_PATH,
    MOCK_RESPONSE_TEXT,
    chat_request,
    key_spend,
    mlpa_headers,
    sse_content,
    wait_for_key_spend,
)


class TestMlpaAuthenticatesToLiteLLM:
    def test_mlpa_completes_through_litellm_with_its_virtual_key(
        self, real_backend_client
    ):
        """The headline check: MLPA's configured virtual key, header name and
        request body are all still accepted by this LiteLLM version."""
        client, token, _ = real_backend_client

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(),
        )

        assert response.status_code == 200, response.text
        content = response.json()["choices"][0]["message"]["content"]
        assert content == MOCK_RESPONSE_TEXT

    def test_requests_are_attributed_to_mlpas_virtual_key(
        self, real_backend_client, proxy
    ):
        """Run completion against MLPA, then validate the spend lands on MLPA
        virtual key
        """
        client, token, _ = real_backend_client
        before = key_spend(proxy, env.MLPA_VIRTUAL_KEY)

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(),
        )
        assert response.status_code == 200, response.text

        after = wait_for_key_spend(proxy, env.MLPA_VIRTUAL_KEY, above=before)
        assert after > before

    def test_mlpa_surfaces_an_auth_failure_instead_of_silently_succeeding(
        self, real_backend_client
    ):
        """MLPA must propogate litellm key errors back up to client, rather than
        reporting 500 error, or some other unexpected rejection.
        """
        client, token, _ = real_backend_client
        bad_headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer sk-mlpa-key-that-was-never-issued",
        }

        with patch(
            "mlpa.core.completions.LITELLM_VIRTUAL_AUTH_HEADERS",
            bad_headers,
        ):
            response = client.post(
                CHAT_COMPLETIONS_PATH,
                headers=mlpa_headers(token),
                json=chat_request(),
            )

        assert response.status_code == 401, (
            "MLPA returned "
            f"{response.status_code} while holding an invalid virtual key -- "
            "either the proxy accepted it or MLPA swallowed the rejection."
        )

    def test_streaming_requests_authenticate_too(self, real_backend_client):
        """MLPA streams via a separate code path (client.stream in
        completions.py) that re-sends the same headers, so it can break
        independently of the buffered path."""
        client, token, _ = real_backend_client

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(stream=True),
        )

        assert response.status_code == 200, response.text
        assert "text/event-stream" in response.headers["content-type"]
        # An upstream auth failure surfaces as an SSE error frame rather than
        # a non-200, so assert on the payload rather than the status alone.
        assert sse_content(response.text) == MOCK_RESPONSE_TEXT, response.text[:500]
