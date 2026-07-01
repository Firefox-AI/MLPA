import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from mlpa.core.classes import AuthorizedSearchRequest
from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_REQUEST_TOO_LARGE,
    ERROR_CODE_UPSTREAM_TIMEOUT,
)
from mlpa.core.metrics import SEARCH_MODEL
from mlpa.core.prometheus_metrics import PrometheusRejectionReason, PrometheusResult
from mlpa.core.search import get_search


def _httpx_encode_json(body: dict) -> bytes:
    """Mirror how httpx (0.28) serializes a ``json=`` body.

    httpx.encode_json uses ``ensure_ascii=False`` then ``.encode("utf-8")``, so a
    lone/unpaired UTF-16 surrogate survives ``json.dumps`` and then raises
    ``UnicodeEncodeError: surrogates not allowed`` on the encode (in prod a 502
    "Failed to proxy request").
    """
    return json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


async def test_get_search_handles_unpaired_surrogate(mocker):
    """A search query truncated mid-emoji (lone ``\\ud83e``) must not break the
    outgoing UTF-8 encode that httpx performs on the request body."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.search.get_http_client", return_value=mock_client)

    req = AuthorizedSearchRequest(
        user="test-user:search",
        service_type="search",
        query="weather in tokyo \ud83e",
        max_results=5,
    )

    await get_search(req)

    _, call_kwargs = mock_client.post.call_args
    sent_json = call_kwargs["json"]
    # Must not raise UnicodeEncodeError the way httpx encodes the body.
    _httpx_encode_json(sent_json)
    assert "\ud83e" not in sent_json["query"]
    assert sent_json["query"].startswith("weather in tokyo ")


async def test_get_search_sanitizes_response_surrogates(mocker):
    """Upstream search results with a lone surrogate must be cleaned before return."""
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": [{"title": "weird \ud83e"}]}
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.search.get_http_client", return_value=mock_client)

    req = AuthorizedSearchRequest(
        user="test-user:search",
        service_type="search",
        query="weather in tokyo",
        max_results=5,
    )

    data = await get_search(req)

    assert "\ud83e" not in data["results"][0]["title"]
    _httpx_encode_json(data)  # must not raise


async def test_get_search_timeout_returns_custom_error_code(mocker, metrics_spy):
    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ReadTimeout("")
    mocker.patch("mlpa.core.search.get_http_client", return_value=mock_client)

    req = AuthorizedSearchRequest(
        user="test-user:search",
        service_type="search",
        query="weather in tokyo",
        max_results=5,
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_search(req)

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == {"error": ERROR_CODE_UPSTREAM_TIMEOUT}
    metrics_spy.assert_only({"search_latency"})
    assert _search_latency_count(metrics_spy, PrometheusResult.ERROR) == 1


def _search_rejection_count(
    spy,
    reason: PrometheusRejectionReason,
    req: AuthorizedSearchRequest,
) -> float:
    return spy.value(
        "search_request_rejections",
        reason=reason,
        model=SEARCH_MODEL,
        service_type=req.service_type,
        purpose=req.purpose,
    )


def _search_latency_count(spy, result: PrometheusResult) -> float:
    return spy.histogram_count("search_latency", result=result)


async def test_get_search_budget_limit_exceeded_records_rejection(mocker, metrics_spy):
    req = AuthorizedSearchRequest(
        user="test-user:search",
        service_type="search",
        purpose="",
        query="weather in tokyo",
        max_results=5,
    )

    mock_response = MagicMock()
    mock_response.text = json.dumps(
        {
            "error": {
                "type": "budget_exceeded",
                "message": "ExceededBudget",
            }
        }
    )
    mock_response.status_code = 429
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Too Many Requests",
        request=MagicMock(),
        response=mock_response,
    )

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.search.get_http_client", return_value=mock_client)

    with pytest.raises(HTTPException) as exc_info:
        await get_search(req)

    assert exc_info.value.status_code == 429
    assert exc_info.value.detail == {"error": ERROR_CODE_BUDGET_LIMIT_EXCEEDED}
    assert exc_info.value.headers == {"Retry-After": "86400"}
    metrics_spy.assert_only({"search_request_rejections", "search_latency"})
    assert (
        _search_rejection_count(
            metrics_spy, PrometheusRejectionReason.BUDGET_EXCEEDED, req
        )
        == 1
    )
    assert _search_latency_count(metrics_spy, PrometheusResult.ERROR) == 1


async def test_get_search_context_window_exceeded_records_rejection(
    mocker, metrics_spy
):
    req = AuthorizedSearchRequest(
        user="test-user:search",
        service_type="search",
        purpose="",
        query="weather in tokyo",
        max_results=5,
    )

    mock_response = MagicMock()
    mock_response.text = "maximum context length exceeded"
    mock_response.status_code = 413
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "Payload Too Large",
        request=MagicMock(),
        response=mock_response,
    )

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.search.get_http_client", return_value=mock_client)

    with pytest.raises(HTTPException) as exc_info:
        await get_search(req)

    assert exc_info.value.status_code == 413
    assert exc_info.value.detail == {"error": ERROR_CODE_REQUEST_TOO_LARGE}
    metrics_spy.assert_only({"search_request_rejections", "search_latency"})
    assert (
        _search_rejection_count(
            metrics_spy, PrometheusRejectionReason.PAYLOAD_TOO_LARGE, req
        )
        == 1
    )
    assert _search_latency_count(metrics_spy, PrometheusResult.ERROR) == 1
