"""
Full ChatRequest field-set acceptance against a real LiteLLM proxy.

test_auth.py and test_routing.py only ever send the minimal
{model, messages} body. A LiteLLM bump that tightens request validation
on any of the other fields MLPA forwards (tools, response_format, seed,
logit_bias, ...) would only show up here, as a 422/400.
"""

from tests.e2e.litellm_compat.helpers import CHAT_COMPLETIONS_PATH, mlpa_headers

FULL_FIELD_BODY = {
    "model": "mock",
    "messages": [{"role": "user", "content": "Hello"}],
    "temperature": 0.7,
    "top_p": 0.9,
    "n": 1,
    "stop": ["\n"],
    "max_completion_tokens": 64,
    "presence_penalty": 0.1,
    "frequency_penalty": 0.1,
    "logit_bias": {"1234": -50},
    "response_format": {"type": "text"},
    "seed": 42,
    "parallel_tool_calls": True,
    "logprobs": True,
    "top_logprobs": 1,
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather for a city",
                "parameters": {
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                },
            },
        }
    ],
    "tool_choice": "auto",
}


class TestChatCompletionRequestShape:
    def test_full_field_chat_body_accepted(self, real_backend_client):
        client, token, _ = real_backend_client
        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=FULL_FIELD_BODY,
        )
        assert response.status_code == 200, response.text

    def test_full_field_chat_body_accepted_streaming(self, real_backend_client):
        """stream=True to also cover the stream_options field
        _build_litellm_body only adds in the streaming path."""
        client, token, _ = real_backend_client
        with client.stream(
            "POST",
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json={**FULL_FIELD_BODY, "stream": True},
        ) as response:
            assert response.status_code == 200, response.read()
