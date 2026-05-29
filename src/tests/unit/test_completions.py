import contextlib
import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from pytest_httpx import HTTPXMock, IteratorStream

from mlpa.core.classes import AuthorizedChatRequest
from mlpa.core.completions import get_completion, stream_completion
from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_INVALID_MODEL_NAME,
    ERROR_CODE_INVALID_REQUEST,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    ERROR_CODE_REQUEST_TOO_LARGE,
    ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED,
    LITELLM_COMPLETIONS_URL,
    LITELLM_HEADER_ATTEMPTED_FALLBACKS,
    LITELLM_HEADER_ATTEMPTED_RETRIES,
    LITELLM_HEADER_MODEL_API_BASE,
    LITELLM_HEADER_RESPONSE_COST,
    LITELLM_HEADER_RESPONSE_DURATION_MS,
    env,
)
from mlpa.core.prometheus_metrics import PrometheusRejectionReason, PrometheusResult
from tests.consts import SAMPLE_REQUEST, SUCCESSFUL_CHAT_RESPONSE


def _latency_count(spy, result: PrometheusResult, req=SAMPLE_REQUEST) -> float:
    return spy.histogram_count(
        "chat_completion_latency",
        result=result,
        model=req.model,
        service_type=req.service_type,
        purpose=req.purpose,
    )


def _rejection_count(
    spy, reason: PrometheusRejectionReason, req=SAMPLE_REQUEST
) -> float:
    return spy.value(
        "chat_request_rejections",
        reason=reason,
        model=req.model,
        service_type=req.service_type,
        purpose=req.purpose,
    )


def _sample_litellm_response_headers(**overrides: str) -> httpx.Headers:
    base = {
        LITELLM_HEADER_MODEL_API_BASE: "https://api.together.xyz/v1",
        LITELLM_HEADER_ATTEMPTED_FALLBACKS: "0",
        LITELLM_HEADER_ATTEMPTED_RETRIES: "0",
        LITELLM_HEADER_RESPONSE_DURATION_MS: "2000",
        LITELLM_HEADER_RESPONSE_COST: "0.001",
    }
    base.update(overrides)
    return httpx.Headers(base)


def _litellm_routing_label_base():
    return {
        "requested_model": SAMPLE_REQUEST.model,
        "backend": "https://api.together.xyz/v1",
        "service_type": SAMPLE_REQUEST.service_type,
        "purpose": SAMPLE_REQUEST.purpose,
    }


async def test_get_completion_success(mocker, metrics_spy):
    """
    Tests the successful execution path of get_completion.
    - Verifies the external API call is made correctly.
    - Verifies metrics are recorded against an isolated registry (real samples,
      real label validation) and asserts the *exact* set of touched metrics —
      a stray write to anything else will fail the test.
    """
    mock_response = MagicMock()
    mock_response.json.return_value = SUCCESSFUL_CHAT_RESPONSE
    mock_response.headers = _sample_litellm_response_headers()
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)

    result_data = await get_completion(SAMPLE_REQUEST)

    mock_client.post.assert_awaited_once()
    _, call_kwargs = mock_client.post.call_args
    sent_json = call_kwargs.get("json", {})
    assert sent_json["model"] == SAMPLE_REQUEST.model
    assert sent_json["messages"] == SAMPLE_REQUEST.messages
    assert sent_json["user"] == SAMPLE_REQUEST.user
    assert sent_json["stream"] == SAMPLE_REQUEST.stream
    assert "service_type" not in sent_json

    metrics_spy.assert_only(
        {
            "chat_tokens",
            "chat_tokens_per_request",
            "chat_completion_latency",
            "litellm_routed_completions",
            "litellm_attempted_fallbacks",
            "litellm_attempted_retries",
            "litellm_reported_duration_seconds",
            "litellm_reported_cost_usd_total",
            "litellm_routed_tokens",
        }
    )

    chat_label_base = {
        "model": SAMPLE_REQUEST.model,
        "service_type": SAMPLE_REQUEST.service_type,
        "purpose": SAMPLE_REQUEST.purpose,
    }
    assert (
        metrics_spy.value("chat_tokens", type="prompt", **chat_label_base)
        == SUCCESSFUL_CHAT_RESPONSE["usage"]["prompt_tokens"]
    )
    assert (
        metrics_spy.value("chat_tokens", type="completion", **chat_label_base)
        == SUCCESSFUL_CHAT_RESPONSE["usage"]["completion_tokens"]
    )
    assert _latency_count(metrics_spy, PrometheusResult.SUCCESS) == 1

    routing = _litellm_routing_label_base()
    assert (
        metrics_spy.value(
            "litellm_routed_completions", **routing, fallback_used="false"
        )
        == 1
    )
    assert (
        metrics_spy.value(
            "litellm_reported_cost_usd_total", **routing, fallback_used="false"
        )
        == 0.001
    )
    assert metrics_spy.histogram_sum("litellm_attempted_fallbacks", **routing) == 0.0
    assert metrics_spy.histogram_sum("litellm_attempted_retries", **routing) == 0.0
    assert (
        metrics_spy.histogram_sum(
            "litellm_reported_duration_seconds", **routing, fallback_used="false"
        )
        == 2.0
    )

    routed_token_types = {
        s.labels["type"]
        for s in metrics_spy.samples("litellm_routed_tokens")
        if s.labels.get("fallback_used") == "false"
    }
    assert routed_token_types == {"prompt", "completion"}

    assert result_data == SUCCESSFUL_CHAT_RESPONSE


async def test_get_completion_litellm_routing_with_fallback(mocker, metrics_spy):
    mock_response = MagicMock()
    mock_response.json.return_value = SUCCESSFUL_CHAT_RESPONSE
    mock_response.headers = _sample_litellm_response_headers(
        **{
            LITELLM_HEADER_ATTEMPTED_FALLBACKS: "1",
            LITELLM_HEADER_MODEL_API_BASE: "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/publishers/google/models/gemini-pro:predict",
        }
    )
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)

    await get_completion(SAMPLE_REQUEST)

    routing = {
        "requested_model": SAMPLE_REQUEST.model,
        "backend": "https://us-central1-aiplatform.googleapis.com/v1/projects/p/locations/us-central1/publishers/google/models/gemini-pro:predict",
        "service_type": SAMPLE_REQUEST.service_type,
        "purpose": SAMPLE_REQUEST.purpose,
    }
    assert (
        metrics_spy.value("litellm_routed_completions", **routing, fallback_used="true")
        == 1
    )
    assert metrics_spy.histogram_sum("litellm_attempted_fallbacks", **routing) == 1.0


async def test_get_completion_litellm_routing_skips_invalid_optional_headers(
    mocker, metrics_spy
):
    mock_response = MagicMock()
    mock_response.json.return_value = SUCCESSFUL_CHAT_RESPONSE
    mock_response.headers = _sample_litellm_response_headers(
        **{
            LITELLM_HEADER_RESPONSE_DURATION_MS: "not-a-float",
            LITELLM_HEADER_RESPONSE_COST: "nan",
        }
    )
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)

    await get_completion(SAMPLE_REQUEST)

    assert "litellm_reported_duration_seconds" not in metrics_spy.touched()
    assert "litellm_reported_cost_usd_total" not in metrics_spy.touched()


async def test_get_completion_litellm_routing_skips_negative_duration_ms(
    mocker, metrics_spy
):
    mock_response = MagicMock()
    mock_response.json.return_value = SUCCESSFUL_CHAT_RESPONSE
    mock_response.headers = _sample_litellm_response_headers(
        **{LITELLM_HEADER_RESPONSE_DURATION_MS: "-1"},
    )
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)

    await get_completion(SAMPLE_REQUEST)

    assert "litellm_reported_duration_seconds" not in metrics_spy.touched()


async def test_get_completion_http_error(mocker, metrics_spy):
    mock_response = MagicMock()
    mock_response.text = "Internal Server Error"
    mock_response.status_code = 500

    mock_http_status_error = httpx.HTTPStatusError(
        "Internal Server Error", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)
    mocker.patch.object(env, "MLPA_DEBUG", False)

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail["error"] == "Upstream service returned an error"

    metrics_spy.assert_only({"chat_completion_latency"})
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_get_completion_network_error(mocker, metrics_spy):
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.TimeoutException("Connection timed out")
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)
    mocker.patch.object(env, "MLPA_DEBUG", True)

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail["error"] == "Connection timed out"

    metrics_spy.assert_only({"chat_completion_latency"})
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_success(
    httpx_mock: HTTPXMock, mock_request, metrics_spy
):
    usage_chunk = b'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 25}}'
    mock_chunks = [b"data: chunk1", b"data: chunk2", usage_chunk, b"data: [DONE]"]

    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        stream=IteratorStream(mock_chunks),
        status_code=200,
        headers=_sample_litellm_response_headers(),
    )

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert received_chunks == mock_chunks

    request = httpx_mock.get_request()
    assert request is not None
    request_body = json.loads(request.content)
    assert request_body["stream"] is True
    assert request_body["stream_options"] == {"include_usage": True}
    assert request_body["user"] == "test-user-123:ai"
    assert request_body["model"] == "test-model"
    assert "service_type" not in request_body

    metrics_spy.assert_only(
        {
            "chat_completion_ttft",
            "chat_tokens",
            "chat_tokens_per_request",
            "chat_completion_latency",
            "litellm_routed_completions",
            "litellm_attempted_fallbacks",
            "litellm_attempted_retries",
            "litellm_reported_duration_seconds",
            "litellm_reported_cost_usd_total",
            "litellm_routed_tokens",
        }
    )

    chat_label_base = {
        "model": SAMPLE_REQUEST.model,
        "service_type": SAMPLE_REQUEST.service_type,
        "purpose": SAMPLE_REQUEST.purpose,
    }
    assert metrics_spy.value("chat_tokens", type="prompt", **chat_label_base) == 10
    assert metrics_spy.value("chat_tokens", type="completion", **chat_label_base) == 25
    assert _latency_count(metrics_spy, PrometheusResult.SUCCESS) == 1
    assert (
        metrics_spy.histogram_count("chat_completion_ttft", model=SAMPLE_REQUEST.model)
        == 1
    )

    routing = _litellm_routing_label_base()
    assert (
        metrics_spy.value(
            "litellm_routed_completions", **routing, fallback_used="false"
        )
        == 1
    )
    assert (
        metrics_spy.value(
            "litellm_reported_cost_usd_total", **routing, fallback_used="false"
        )
        == 0.001
    )
    assert (
        metrics_spy.histogram_sum(
            "litellm_reported_duration_seconds", **routing, fallback_used="false"
        )
        == 2.0
    )
    routed_token_types = {
        s.labels["type"]
        for s in metrics_spy.samples("litellm_routed_tokens")
        if s.labels.get("fallback_used") == "false"
    }
    assert routed_token_types == {"prompt", "completion"}


async def test_stream_completion_litellm_routing_with_fallback(
    httpx_mock: HTTPXMock, mock_request, metrics_spy
):
    usage_chunk = b'data: {"usage": {"prompt_tokens": 10, "completion_tokens": 25}}'
    mock_chunks = [b"data: chunk1", usage_chunk, b"data: [DONE]"]

    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        stream=IteratorStream(mock_chunks),
        status_code=200,
        headers=_sample_litellm_response_headers(
            **{LITELLM_HEADER_ATTEMPTED_FALLBACKS: "2"},
        ),
    )

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]
    assert received_chunks == mock_chunks

    routing = _litellm_routing_label_base()
    assert (
        metrics_spy.value("litellm_routed_completions", **routing, fallback_used="true")
        == 1
    )
    assert metrics_spy.histogram_sum("litellm_attempted_fallbacks", **routing) == 2.0


async def test_get_completion_budget_limit_exceeded_429(mocker, metrics_spy):
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "error": {
                "message": "Budget has been exceeded! Current cost: 0.001565, Max budget: 0.001",
                "type": "budget_exceeded",
                "code": "400",
            }
        }
    )
    mock_response.status_code = 429

    mock_http_status_error = httpx.HTTPStatusError(
        "Too Many Requests", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {"error": 1}
    assert exc_info.value.headers == {"Retry-After": "86400"}

    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.BUDGET_EXCEEDED) == 1
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_get_completion_budget_limit_exceeded_400(mocker, metrics_spy):
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "error": {
                "message": "Budget has been exceeded! Current cost: 0.001565, Max budget: 0.001",
                "type": "budget_exceeded",
                "code": "400",
            }
        }
    )
    mock_response.status_code = 400

    mock_http_status_error = httpx.HTTPStatusError(
        "Bad Request", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {"error": 1}
    assert exc_info.value.headers == {"Retry-After": "86400"}

    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.BUDGET_EXCEEDED) == 1


async def test_get_completion_rate_limit_exceeded(mocker, metrics_spy):
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "error": {
                "message": "Rate limit exceeded. TPM: 1000/500",
                "type": "rate_limit_exceeded",
                "code": "429",
            }
        }
    )
    mock_response.status_code = 429

    mock_http_status_error = httpx.HTTPStatusError(
        "Too Many Requests", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {"error": 2}
    assert exc_info.value.headers == {"Retry-After": "60"}

    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.RATE_LIMITED) == 1


async def test_get_completion_400_non_rate_limit_error(mocker, metrics_spy):
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "error": {
                "message": "Invalid request parameters",
                "type": "invalid_request",
                "code": "400",
            }
        }
    )
    mock_response.status_code = 400

    mock_http_status_error = httpx.HTTPStatusError(
        "Bad Request", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)
    mocker.patch.object(env, "MLPA_DEBUG", False)

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {"error": "Upstream service returned an error"}
    metrics_spy.assert_only({"chat_completion_latency"})


async def test_get_completion_429_non_rate_limit_error(mocker, metrics_spy):
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {"error": {"message": "Some other error", "type": "other_error", "code": "429"}}
    )
    mock_response.status_code = 429

    mock_http_status_error = httpx.HTTPStatusError(
        "Too Many Requests", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)
    mocker.patch.object(env, "MLPA_DEBUG", False)

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {"error": "Upstream service returned an error"}
    metrics_spy.assert_only({"chat_completion_latency"})


async def test_get_completion_upstream_rate_limit_error(mocker, metrics_spy):
    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {"status": "RESOURCE_EXHAUSTED", "type": "throttling_error"}
    )
    mock_response.status_code = 429

    mock_http_status_error = httpx.HTTPStatusError(
        "Too Many Requests", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)
    mocker.patch.object(env, "MLPA_DEBUG", False)

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {"error": ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED}
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.RATE_LIMITED) == 1
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_get_completion_context_window_exceeded(mocker, metrics_spy):
    error_text = (
        "litellm.ContextWindowExceededError: This model's maximum context length "
        "is 128000 tokens. However, your messages resulted in 496095 tokens."
    )
    mock_response = MagicMock()
    mock_response.text = error_text
    mock_response.status_code = 400

    mock_http_status_error = httpx.HTTPStatusError(
        "Bad Request", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)
    mock_logger = mocker.patch("mlpa.core.completions.logger")

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == {"error": ERROR_CODE_REQUEST_TOO_LARGE}
    mock_logger.warning.assert_called_once()
    assert "Context window exceeded" in str(mock_logger.warning.call_args)
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert (
        _rejection_count(metrics_spy, PrometheusRejectionReason.PAYLOAD_TOO_LARGE) == 1
    )
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_get_completion_invalid_model_name(mocker, metrics_spy):
    error_text = json.dumps(
        {
            "error": "/chat/completions: Invalid model name passed in model=moz-summarizarion. "
            "Call `/v1/models` to view available models for your key."
        }
    )
    mock_response = MagicMock()
    mock_response.text = error_text
    mock_response.status_code = 400

    mock_http_status_error = httpx.HTTPStatusError(
        "Bad Request", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)
    mock_logger = mocker.patch("mlpa.core.completions.logger")

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {"error": ERROR_CODE_INVALID_MODEL_NAME}
    mock_logger.warning.assert_called_once()
    assert "Invalid model name" in str(mock_logger.warning.call_args)
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert (
        _rejection_count(metrics_spy, PrometheusRejectionReason.INVALID_MODEL_NAME) == 1
    )
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_get_completion_invalid_request_vertex(mocker, metrics_spy):
    error_text = (
        "litellm.BadRequestError: Vertex_aiException BadRequestError - "
        '[{"error": {"code": 400, "message": "Expected a valid JSON object in the request", '
        '"status": "INVALID_ARGUMENT"}}]'
    )
    mock_response = MagicMock()
    mock_response.text = error_text
    mock_response.status_code = 400

    mock_http_status_error = httpx.HTTPStatusError(
        "Bad Request", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)
    mock_logger = mocker.patch("mlpa.core.completions.logger")

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {"error": ERROR_CODE_INVALID_REQUEST}
    mock_logger.warning.assert_called_once()
    assert "Invalid request" in str(mock_logger.warning.call_args)
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.INVALID_REQUEST) == 1
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_get_completion_429_invalid_json(mocker, metrics_spy):
    mock_response = MagicMock()
    mock_response.text = "Invalid JSON response"
    mock_response.status_code = 429

    mock_http_status_error = httpx.HTTPStatusError(
        "Too Many Requests", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_status_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)
    mocker.patch.object(env, "MLPA_DEBUG", False)

    with pytest.raises(HTTPException) as exc_info:
        await get_completion(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {"error": "Upstream service returned an error"}
    metrics_spy.assert_only({"chat_completion_latency"})


async def test_stream_completion_budget_limit_exceeded_429(
    httpx_mock: HTTPXMock, mocker, mock_request, metrics_spy
):
    error_response = json.dumps(
        {
            "error": {
                "message": "Budget has been exceeded! Current cost: 0.001565, Max budget: 0.001",
                "type": "budget_exceeded",
                "code": "400",
            }
        }
    )
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=error_response.encode(),
        status_code=429,
    )

    mock_logger = mocker.patch("mlpa.core.completions.logger")

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]
    assert len(received_chunks) == 1
    assert (
        received_chunks[0]
        == f'data: {{"error": {ERROR_CODE_BUDGET_LIMIT_EXCEEDED}}}\n\n'.encode()
    )
    mock_logger.warning.assert_called_once()
    assert "Budget limit exceeded" in str(mock_logger.warning.call_args)
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.BUDGET_EXCEEDED) == 1
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_budget_limit_exceeded_400(
    httpx_mock: HTTPXMock, mocker, mock_request, metrics_spy
):
    error_response = json.dumps(
        {
            "error": {
                "message": "Budget has been exceeded! Current cost: 0.001565, Max budget: 0.001",
                "type": "budget_exceeded",
                "code": "400",
            }
        }
    )
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=error_response.encode(),
        status_code=400,
    )

    mock_logger = mocker.patch("mlpa.core.completions.logger")

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert len(received_chunks) == 1
    assert (
        received_chunks[0]
        == f'data: {{"error": {ERROR_CODE_BUDGET_LIMIT_EXCEEDED}}}\n\n'.encode()
    )
    mock_logger.warning.assert_called_once()
    assert "Budget limit exceeded" in str(mock_logger.warning.call_args)
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.BUDGET_EXCEEDED) == 1
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_rate_limit_exceeded(
    httpx_mock: HTTPXMock, mocker, mock_request, metrics_spy
):
    error_response = json.dumps(
        {
            "error": {
                "message": "Rate limit exceeded. TPM: 1000/500",
                "type": "rate_limit_exceeded",
                "code": "429",
            }
        }
    )
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=error_response.encode(),
        status_code=429,
    )

    mock_logger = mocker.patch("mlpa.core.completions.logger")

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert len(received_chunks) == 1
    assert (
        received_chunks[0]
        == f'data: {{"error": {ERROR_CODE_RATE_LIMIT_EXCEEDED}}}\n\n'.encode()
    )
    mock_logger.warning.assert_called_once()
    assert "Rate limit exceeded" in str(mock_logger.warning.call_args)
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.RATE_LIMITED) == 1
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_context_window_exceeded(
    httpx_mock: HTTPXMock, mocker, mock_request, metrics_spy
):
    error_text = (
        "litellm.ContextWindowExceededError: This model's maximum context length "
        "is 128000 tokens. However, your messages resulted in 496095 tokens."
    )
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=error_text.encode(),
        status_code=400,
    )

    mock_logger = mocker.patch("mlpa.core.completions.logger")

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert len(received_chunks) == 1
    assert (
        received_chunks[0]
        == f'data: {{"error": {ERROR_CODE_REQUEST_TOO_LARGE}}}\n\n'.encode()
    )
    mock_logger.warning.assert_called_once()
    assert "Context window exceeded" in str(mock_logger.warning.call_args)
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert (
        _rejection_count(metrics_spy, PrometheusRejectionReason.PAYLOAD_TOO_LARGE) == 1
    )
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_invalid_model_name(
    httpx_mock: HTTPXMock, mocker, mock_request, metrics_spy
):
    error_response = json.dumps(
        {
            "error": "/chat/completions: Invalid model name passed in model=moz-summarizarion. "
            "Call `/v1/models` to view available models for your key."
        }
    )
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=error_response.encode(),
        status_code=400,
    )

    mock_logger = mocker.patch("mlpa.core.completions.logger")

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert len(received_chunks) == 1
    assert (
        received_chunks[0]
        == f'data: {{"error": {ERROR_CODE_INVALID_MODEL_NAME}}}\n\n'.encode()
    )
    mock_logger.warning.assert_called_once()
    assert "Invalid model name" in str(mock_logger.warning.call_args)
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert (
        _rejection_count(metrics_spy, PrometheusRejectionReason.INVALID_MODEL_NAME) == 1
    )
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_invalid_request_vertex(
    httpx_mock: HTTPXMock, mocker, mock_request, metrics_spy
):
    error_response = (
        "litellm.BadRequestError: Vertex_aiException BadRequestError - "
        '[{"error": {"code": 400, "message": "Expected a valid JSON object in the request", '
        '"status": "INVALID_ARGUMENT"}}]'
    )
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=error_response.encode(),
        status_code=400,
    )

    mock_logger = mocker.patch("mlpa.core.completions.logger")

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert len(received_chunks) == 1
    assert (
        received_chunks[0]
        == f'data: {{"error": {ERROR_CODE_INVALID_REQUEST}}}\n\n'.encode()
    )
    mock_logger.warning.assert_called_once()
    assert "Invalid request" in str(mock_logger.warning.call_args)
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.INVALID_REQUEST) == 1
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_400_non_rate_limit_error(
    httpx_mock: HTTPXMock, mocker, mock_request, metrics_spy
):
    error_response = json.dumps(
        {
            "error": {
                "message": "Invalid request parameters",
                "type": "invalid_request",
                "code": "400",
            }
        }
    )
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=error_response.encode(),
        status_code=400,
    )

    mock_logger = mocker.patch("mlpa.core.utils.logger")
    mocker.patch.object(env, "MLPA_DEBUG", False)

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert len(received_chunks) == 1
    assert (
        received_chunks[0]
        == b'data: {"code": 400, "error": "Upstream service returned an error"}\n\n'
    )
    mock_logger.error.assert_called_once()
    metrics_spy.assert_only({"chat_completion_latency"})
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_429_non_rate_limit_error(
    httpx_mock: HTTPXMock, mocker, mock_request, metrics_spy
):
    error_response = json.dumps(
        {"error": {"message": "Some other error", "type": "other_error", "code": "429"}}
    )
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=error_response.encode(),
        status_code=429,
    )

    mock_logger = mocker.patch("mlpa.core.utils.logger")
    mocker.patch.object(env, "MLPA_DEBUG", False)

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert len(received_chunks) == 1
    assert (
        received_chunks[0]
        == b'data: {"code": 429, "error": "Upstream service returned an error"}\n\n'
    )
    mock_logger.error.assert_called_once()
    metrics_spy.assert_only({"chat_completion_latency"})
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_upstream_rate_limit_error(
    httpx_mock: HTTPXMock, mock_request, metrics_spy
):
    error_response = json.dumps(
        {"status": "RESOURCE_EXHAUSTED", "type": "throttling_error"}
    )
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=error_response.encode(),
        status_code=429,
    )

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert received_chunks == [
        f'data: {{"error": {ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED}}}\n\n'.encode()
    ]
    metrics_spy.assert_only({"chat_request_rejections", "chat_completion_latency"})
    assert _rejection_count(metrics_spy, PrometheusRejectionReason.RATE_LIMITED) == 1
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_429_invalid_json(
    httpx_mock: HTTPXMock, mocker, mock_request, metrics_spy
):
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        content=b"Invalid JSON response",
        status_code=429,
    )

    mock_logger = mocker.patch("mlpa.core.utils.logger")
    mocker.patch.object(env, "MLPA_DEBUG", False)

    received_chunks = [
        chunk async for chunk in stream_completion(SAMPLE_REQUEST, mock_request)
    ]

    assert len(received_chunks) == 1
    assert (
        received_chunks[0]
        == b'data: {"code": 429, "error": "Upstream service returned an error"}\n\n'
    )
    mock_logger.error.assert_called_once()
    metrics_spy.assert_only({"chat_completion_latency"})
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_stream_completion_exception_after_streaming_started(
    httpx_mock: HTTPXMock, mock_request, metrics_spy
):
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        status_code=500,
        text="Internal Server Error",
    )

    received_chunks = []
    async for chunk in stream_completion(SAMPLE_REQUEST, mock_request):
        received_chunks.append(chunk)

    assert len(received_chunks) == 1
    assert b"error" in received_chunks[0]
    metrics_spy.assert_only({"chat_completion_latency"})
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_get_completion_preserves_tools(mocker, metrics_spy):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_browsing_history",
                "description": "Search browsing history",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "searchTerm": {"type": "string"},
                        "startTs": {"type": "string"},
                        "endTs": {"type": "string"},
                    },
                },
            },
        }
    ]
    tool_choice = "auto"

    request_with_tools = AuthorizedChatRequest(
        user="test-user-123:ai",
        service_type="ai",
        purpose="chat",
        model="test-model",
        messages=[
            {"role": "user", "content": "What mario sites did i look at yesterday?"}
        ],
        temperature=0.7,
        top_p=0.9,
        max_completion_tokens=150,
        tools=tools,
        tool_choice=tool_choice,
    )

    mock_response = MagicMock()
    mock_response.json.return_value = SUCCESSFUL_CHAT_RESPONSE
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)

    await get_completion(request_with_tools)

    mock_client.post.assert_awaited_once()
    _, call_kwargs = mock_client.post.call_args
    sent_json = call_kwargs.get("json", {})

    assert sent_json["tools"] == tools
    assert sent_json["tool_choice"] == tool_choice
    assert sent_json["model"] == request_with_tools.model
    assert sent_json["messages"] == request_with_tools.messages

    # Tool-request and tool-call metrics should fire.
    assert "chat_requests_with_tools" in metrics_spy.touched()


async def test_stream_completion_preserves_tools(
    httpx_mock: HTTPXMock, mock_request, metrics_spy
):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "search_browsing_history",
                "description": "Search browsing history",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "searchTerm": {"type": "string"},
                        "startTs": {"type": "string"},
                        "endTs": {"type": "string"},
                    },
                },
            },
        }
    ]
    tool_choice = {"type": "function", "function": {"name": "search_browsing_history"}}

    request_with_tools = AuthorizedChatRequest(
        user="test-user-123:ai",
        service_type="ai",
        purpose="chat",
        model="test-model",
        messages=[
            {"role": "user", "content": "What mario sites did i look at yesterday?"}
        ],
        temperature=0.7,
        top_p=0.9,
        max_completion_tokens=150,
        tools=tools,
        tool_choice=tool_choice,
    )

    usage_chunk = b'data: {"usage": {"prompt_tokens": 50, "completion_tokens": 30}}'
    mock_chunks = [
        b'data: {"choices":[{"delta":{"tool_calls":[{"id":"call_123","type":"function","function":{"name":"search_browsing_history","arguments":"{\\"searchTerm\\":\\"mario\\"}"}}]}}]}\n\n',
        usage_chunk,
        b"data: [DONE]\n\n",
    ]

    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        stream=IteratorStream(mock_chunks),
        status_code=200,
    )

    received_chunks = [
        chunk async for chunk in stream_completion(request_with_tools, mock_request)
    ]

    request = httpx_mock.get_request()
    assert request is not None
    request_body = json.loads(request.content)

    assert request_body["tools"] == tools
    assert request_body["tool_choice"] == tool_choice
    assert request_body["model"] == request_with_tools.model
    assert request_body["messages"] == request_with_tools.messages
    assert request_body["stream_options"] == {"include_usage": True}

    assert len(received_chunks) == len(mock_chunks)
    assert b"tool_calls" in received_chunks[0]
    assert "chat_tool_calls" in metrics_spy.touched()
    assert "chat_requests_with_tools" in metrics_spy.touched()


def _assert_error_latency(spy) -> None:
    assert _latency_count(spy, PrometheusResult.ERROR) == 1


def _patch_mock_stream_client(mocker, aiter_bytes_fn, capture: dict | None = None):
    """Patch get_http_client with a mock that streams via aiter_bytes_fn.

    capture: if provided, stream call kwargs are merged into it (for timeout inspection).
    """
    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.headers = {}
    mock_response.aiter_bytes = aiter_bytes_fn

    @contextlib.asynccontextmanager
    async def _mock_stream(*args, **kwargs):
        if capture is not None:
            capture.update(kwargs)
        yield mock_response

    mock_client = MagicMock()
    mock_client.stream = _mock_stream
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)


async def test_stream_sends_error_sse_on_exception_after_streaming_started(
    mocker, mock_request, metrics_spy
):
    """
    Exception mid-stream (after first chunk) must still send an error SSE.
    """
    role_chunk = (
        b'data: {"choices":[{"delta":{"role":"assistant","content":null}}]}\n\n'
    )

    async def _failing_aiter_bytes():
        yield role_chunk
        raise RuntimeError("Connection dropped mid-stream")

    _patch_mock_stream_client(mocker, _failing_aiter_bytes)

    received = [c async for c in stream_completion(SAMPLE_REQUEST, mock_request)]

    assert len(received) == 2, (
        f"Expected [role_chunk, error_SSE], got {len(received)} chunk(s)."
    )
    assert received[0] == role_chunk
    assert b'"error"' in received[1], "Second chunk must be an error SSE frame"
    _assert_error_latency(metrics_spy)


async def test_stream_sends_error_sse_on_empty_200_response(
    httpx_mock: HTTPXMock, mock_request, metrics_spy
):
    """
    LiteLLM returns 200 with an empty body (zero SSE chunks) — must yield error SSE.
    """
    httpx_mock.add_response(
        method="POST",
        url=LITELLM_COMPLETIONS_URL,
        stream=IteratorStream([]),
        status_code=200,
        headers=_sample_litellm_response_headers(),
    )

    received = [c async for c in stream_completion(SAMPLE_REQUEST, mock_request)]

    assert len(received) == 1, (
        f"Expected exactly one error SSE chunk, got {len(received)}."
    )
    assert b'"error"' in received[0], "Chunk must be an error SSE frame"
    _assert_error_latency(metrics_spy)


async def test_stream_completion_client_disconnect_records_abort(
    mocker, mock_request, metrics_spy
):
    """
    Client disconnect mid-stream tears the generator down via GeneratorExit at
    the paused `yield chunk` (this is what Starlette does when the client goes
    away). Even when the disconnect poller has not fired yet — `is_disconnected`
    still returns False, so `disconnect_event` is unset — this must be recorded
    as ABORT, not ERROR. Otherwise normal client cancellations pollute the error
    rate.
    """
    role_chunk = (
        b'data: {"choices":[{"delta":{"role":"assistant","content":null}}]}\n\n'
    )

    async def _aiter_bytes():
        yield role_chunk
        yield b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'

    _patch_mock_stream_client(mocker, _aiter_bytes)

    gen = stream_completion(SAMPLE_REQUEST, mock_request)
    first = await gen.__anext__()
    assert first == role_chunk

    # Client goes away: the response generator is closed mid-stream.
    await gen.aclose()

    assert _latency_count(metrics_spy, PrometheusResult.ABORT) == 1
    assert _latency_count(metrics_spy, PrometheusResult.ERROR) == 0


async def test_stream_uses_httpx_timeout_object_preserving_pool_timeout(
    mocker, mock_request, metrics_spy
):
    """
    stream_completion must pass an httpx.Timeout object so per-phase timeouts
    (in particular `pool`) are preserved.
    """
    captured = {}

    async def _empty_aiter_bytes():
        if False:
            yield

    _patch_mock_stream_client(mocker, _empty_aiter_bytes, capture=captured)

    _ = [c async for c in stream_completion(SAMPLE_REQUEST, mock_request)]

    timeout = captured.get("timeout")
    assert isinstance(timeout, httpx.Timeout), (
        f"Expected httpx.Timeout, got {type(timeout).__name__}."
    )
    assert timeout.read == env.STREAMING_TIMEOUT_SECONDS
    assert timeout.pool == env.HTTPX_POOL_TIMEOUT_SECONDS
