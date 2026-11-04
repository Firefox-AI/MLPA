"""The resolved upstream key labels accounting without changing the public API."""

import json
from contextlib import asynccontextmanager

import httpx
import pytest
from pytest_httpx import IteratorStream

from mlpa.core.completions import get_completion
from mlpa.core.config import (
    LITELLM_COMPLETIONS_URL,
    LITELLM_HEADER_KEY_ALIAS,
    LITELLM_HEADER_RESPONSE_COST,
    LITELLM_SEARCH_URL,
    LITELLM_VIRTUAL_AUTH_HEADERS,
    env,
)
from mlpa.core.logger import logger
from tests.consts import SAMPLE_REQUEST, SUCCESSFUL_CHAT_RESPONSE, TEST_FXA_TOKEN


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("allow_custom", [False, True])
@pytest.mark.parametrize("status", [200, 429])
def test_chat_attribution_is_from_upstream_and_stays_internal(
    mocked_client_integration, mocker, metrics_spy, stream, allow_custom, status
):
    mocker.patch.object(env, "ALLOW_CUSTOM_VIRTUAL_KEY", allow_custom)
    mocker.patch("mlpa.run.get_completion", get_completion)
    alias = "evals-stage" if allow_custom else "MLPA2"
    headers = {LITELLM_HEADER_KEY_ALIAS: alias, LITELLM_HEADER_RESPONSE_COST: "0.012"}
    payload = (
        SUCCESSFUL_CHAT_RESPONSE
        if status == 200
        else {
            "error": {"message": "Budget has been exceeded!", "type": "budget_exceeded"}
        }
    )
    wire = (
        b'data: {"usage":{"prompt_tokens":10,"completion_tokens":2}}\n\n'
        b"data: [DONE]\n\n"
        if status == 200
        else json.dumps(payload).encode()
    )
    upstream = mocker.AsyncMock()
    upstream.post.return_value = httpx.Response(
        status,
        headers=headers,
        json=payload,
        request=httpx.Request("POST", LITELLM_COMPLETIONS_URL),
    )

    @asynccontextmanager
    async def stream_response(*args, **kwargs):
        yield httpx.Response(
            status,
            headers=headers,
            stream=IteratorStream([wire]),
            request=httpx.Request("POST", LITELLM_COMPLETIONS_URL),
        )

    upstream.stream = mocker.Mock(side_effect=stream_response)
    mocker.patch("mlpa.core.completions.get_http_client", return_value=upstream)
    records = []
    sink = logger.add(records.append, format="{message}")
    try:
        response = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
                "purpose": "chat",
                "litellm-virtual-key": "sk-test-eval-secret",
                LITELLM_HEADER_KEY_ALIAS: "client-spoof",
            },
            json={
                "model": SAMPLE_REQUEST.model,
                "messages": [{"role": "user", "content": "hi"}],
                "stream": stream,
                "metadata": {"key_alias": "client-spoof"},
            },
        )
    finally:
        logger.remove(sink)

    assert response.status_code == (200 if stream else status)
    assert LITELLM_HEADER_KEY_ALIAS not in response.headers
    assert alias not in response.text
    if status == 200:
        if stream:
            assert response.content == wire
        else:
            assert response.json() == SUCCESSFUL_CHAT_RESPONSE
    send = upstream.stream if stream else upstream.post
    outgoing = send.call_args.kwargs
    assert outgoing["headers"]["Authorization"] == (
        "Bearer sk-test-eval-secret"
        if allow_custom
        else LITELLM_VIRTUAL_AUTH_HEADERS["Authorization"]
    )
    assert LITELLM_HEADER_KEY_ALIAS not in outgoing["headers"]
    assert "client-spoof" not in json.dumps(outgoing["json"])
    attribution = [
        r.record for r in records if r.record["message"] == "LiteLLM response received"
    ]
    assert len(attribution) == 1
    assert attribution[0]["extra"]["key_alias"] == alias
    assert attribution[0]["extra"]["upstream_status_code"] == status
    assert "sk-test-eval-secret" not in json.dumps(attribution[0]["extra"])
    costs = [
        sample
        for sample in metrics_spy.samples("litellm_reported_cost_usd_total")
        if sample.name.endswith("_total")
    ]
    if status == 200:
        assert len(costs) == 1
        assert costs[0].labels["key_alias"] == alias
        assert costs[0].value == pytest.approx(0.012)
        assert {
            sample.labels["key_alias"]
            for sample in metrics_spy.samples("litellm_routed_tokens")
        } == {alias}
    else:
        assert costs == []


@pytest.mark.parametrize("status", [200, 429])
def test_search_logs_resolved_alias_without_exposing_it(
    mocked_client_integration, mocker, status
):
    upstream = mocker.AsyncMock()
    upstream.post.return_value = httpx.Response(
        status,
        headers={LITELLM_HEADER_KEY_ALIAS: "evals-search-stage"},
        json=(
            {"results": []}
            if status == 200
            else {"error": {"message": "Budget has been exceeded!"}}
        ),
        request=httpx.Request("POST", f"{LITELLM_SEARCH_URL}/exa-search"),
    )
    mocker.patch("mlpa.core.search.get_http_client", return_value=upstream)
    records = []
    sink = logger.add(records.append, format="{message}")
    try:
        response = mocked_client_integration.post(
            "/v1/search",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "search",
                LITELLM_HEADER_KEY_ALIAS: "client-spoof",
            },
            json={"query": "browser news", "max_results": 5},
        )
    finally:
        logger.remove(sink)
    assert response.status_code == status
    assert LITELLM_HEADER_KEY_ALIAS not in response.headers
    assert "evals-search-stage" not in response.text
    attribution = [
        r.record for r in records if r.record["message"] == "LiteLLM response received"
    ]
    assert len(attribution) == 1
    assert attribution[0]["extra"]["key_alias"] == "evals-search-stage"
    assert attribution[0]["extra"]["service_type"] == "search"
