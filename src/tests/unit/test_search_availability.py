import asyncio
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from starlette.requests import ClientDisconnect

from mlpa import run
from mlpa.core.classes import AuthorizedSearchRequest
from mlpa.core.config import ERROR_CODE_MAX_GUESTS_REACHED, ERROR_CODE_MAX_USERS_REACHED
from mlpa.core.metrics import record_search_availability_for
from mlpa.core.middleware import register_middleware
from mlpa.core.prometheus_metrics import AvailabilityReason, availability_outcome_for
from mlpa.core.search import get_search
from tests.consts import MOCK_LITELLM_GLOBAL_BUDGET_ERROR_TEXT


@pytest.fixture
def search_boundary(mocker):
    app = FastAPI()
    app.post("/v1/search")(run.search)
    register_middleware(app)
    provisioning = mocker.patch(
        "mlpa.core.metrics.get_or_create_user",
        AsyncMock(return_value=({"blocked": False}, False)),
    )
    mocker.patch(
        "mlpa.core.auth.authorize.fxa_auth", AsyncMock(return_value={"user": "u"})
    )
    upstream = AsyncMock()
    upstream.post.return_value = httpx.Response(
        200,
        json={"results": []},
        request=httpx.Request("POST", "http://upstream/search"),
    )
    mocker.patch("mlpa.core.search.get_http_client", return_value=upstream)
    mocker.patch("mlpa.core.search.redis_service.update_contracts", AsyncMock())
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, upstream, provisioning


def _post(client, *, client_country="", **kwargs):
    return client.post(
        "/v1/search",
        headers={
            "authorization": "Bearer token",
            "service-type": "search",
            "X-Geo-Country": client_country,
        },
        json={"query": "weather", "max_results": 5},
        **kwargs,
    )


def _assert_once(
    spy, reason, service_type="search", purpose="", client_country="other"
):
    assert (
        spy.value(
            "search_availability",
            outcome=availability_outcome_for(reason),
            reason=reason,
            model="exa-search",
            service_type=service_type,
            purpose=purpose,
        )
        == 1
    )
    assert (
        sum(
            s.value
            for s in spy.samples("search_availability")
            if s.name.endswith("_total")
        )
        == 1
    )
    assert not spy.samples("chat_availability")
    assert (
        spy.value(
            "search_availability_by_country",
            outcome=availability_outcome_for(reason),
            client_country=client_country,
            service_type=service_type,
        )
        == 1
    )
    assert (
        sum(
            s.value
            for s in spy.samples("search_availability_by_country")
            if s.name.endswith("_total")
        )
        == 1
    )
    for sample in spy.samples("search_availability_by_country"):
        assert set(sample.labels) == {"outcome", "client_country", "service_type"}


@pytest.mark.parametrize(
    "results", [[], [{"title": "Weather", "url": "https://example.com"}]]
)
def test_valid_search_including_zero_results(search_boundary, metrics_spy, results):
    client, upstream, _ = search_boundary
    upstream.post.return_value = httpx.Response(
        200,
        json={"results": results},
        request=httpx.Request("POST", "http://upstream/"),
    )
    assert _post(client).status_code == 200
    _assert_once(metrics_spy, AvailabilityReason.VALID_RESPONSE)


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"results": None},
        {"results": "bad"},
        {"results": [None]},
        {"results": [], "error": "failed"},
    ],
)
def test_malformed_search_response(search_boundary, metrics_spy, body):
    client, upstream, _ = search_boundary
    upstream.post.return_value = httpx.Response(
        200, content=json.dumps(body), request=httpx.Request("POST", "http://upstream/")
    )
    assert _post(client).status_code == 502
    _assert_once(metrics_spy, AvailabilityReason.INVALID_RESPONSE)


def test_non_json_search_response_records_upstream_error(search_boundary, metrics_spy):
    client, upstream, _ = search_boundary
    upstream.post.return_value = httpx.Response(
        200, content="not json", request=httpx.Request("POST", "http://upstream/")
    )
    assert _post(client).status_code == 502
    _assert_once(metrics_spy, AvailabilityReason.UPSTREAM_ERROR)


@pytest.mark.parametrize(
    "exception", [httpx.ReadTimeout("timeout"), httpx.ConnectError("offline")]
)
def test_search_transport_failure(search_boundary, metrics_spy, exception):
    client, upstream, _ = search_boundary
    upstream.post.side_effect = exception
    assert _post(client).status_code == 502
    _assert_once(metrics_spy, AvailabilityReason.UPSTREAM_ERROR)


@pytest.mark.parametrize(
    "status,body,returned,reason",
    [
        (500, "upstream failed", 500, AvailabilityReason.UPSTREAM_ERROR),
        (
            429,
            '{"error":{"type":"budget_exceeded","message":"ExceededBudget"}}',
            429,
            AvailabilityReason.BUDGET_EXCEEDED,
        ),
        (
            400,
            MOCK_LITELLM_GLOBAL_BUDGET_ERROR_TEXT,
            500,
            AvailabilityReason.GLOBAL_BUDGET_EXCEEDED,
        ),
        (
            429,
            '{"error":{"message":"rate limit exceeded"}}',
            429,
            AvailabilityReason.RATE_LIMITED_PLATFORM,
        ),
        (
            429,
            "litellm.RateLimitError: Exa_aiException - rate limit exceeded",
            429,
            AvailabilityReason.RATE_LIMITED_UPSTREAM,
        ),
        (413, "too large", 413, AvailabilityReason.PAYLOAD_TOO_LARGE),
    ],
)
def test_upstream_error_classification(
    search_boundary, metrics_spy, status, body, returned, reason
):
    client, upstream, _ = search_boundary
    upstream.post.return_value = httpx.Response(
        status, content=body, request=httpx.Request("POST", "http://upstream/")
    )
    assert _post(client).status_code == returned
    _assert_once(metrics_spy, reason)


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, AvailabilityReason.AUTH_REJECTED),
        (400, AvailabilityReason.INVALID_AUTH_REQUEST),
        (500, None),
    ],
)
def test_auth_errors(search_boundary, metrics_spy, mocker, status, reason):
    client, upstream, provisioning = search_boundary
    mocker.patch(
        "mlpa.core.auth.authorize.fxa_auth",
        AsyncMock(side_effect=HTTPException(status)),
    )
    assert _post(client).status_code == status
    if reason is None:
        # Match chat: auth errors other than 400/401 propagate unrecorded.
        assert not metrics_spy.samples("search_availability")
    else:
        _assert_once(metrics_spy, reason)
    provisioning.assert_not_awaited()
    upstream.post.assert_not_awaited()


@pytest.mark.parametrize(
    "status,error,reason",
    [
        (403, ERROR_CODE_MAX_USERS_REACHED, AvailabilityReason.SIGNUP_CAP_EXCEEDED),
        (403, ERROR_CODE_MAX_GUESTS_REACHED, AvailabilityReason.GUEST_CAP_EXCEEDED),
        (500, "database unavailable", AvailabilityReason.PROVISIONING_FAILURE),
    ],
)
def test_provisioning_errors(search_boundary, metrics_spy, status, error, reason):
    client, upstream, provisioning = search_boundary
    provisioning.side_effect = HTTPException(status, detail={"error": error})
    assert _post(client).status_code == status
    _assert_once(metrics_spy, reason)
    upstream.post.assert_not_awaited()


def test_blocked_user(search_boundary, metrics_spy):
    client, upstream, provisioning = search_boundary
    provisioning.return_value = ({"blocked": True}, False)
    assert _post(client).status_code == 403
    _assert_once(metrics_spy, AvailabilityReason.BLOCKED)
    upstream.post.assert_not_awaited()


def test_invalid_service_type_for_search(search_boundary, metrics_spy):
    client, upstream, _ = search_boundary
    response = client.post(
        "/v1/search",
        headers={"authorization": "Bearer token", "service-type": "ai"},
        json={"query": "weather", "max_results": 5},
    )
    assert response.status_code == 400
    _assert_once(
        metrics_spy,
        AvailabilityReason.INVALID_SERVICE_TYPE_FOR_MODEL,
        service_type="ai",
    )
    upstream.post.assert_not_awaited()


def test_framework_validation_is_outside_availability_handlers(
    search_boundary, metrics_spy
):
    client, upstream, _ = search_boundary
    response = client.post(
        "/v1/search",
        headers={
            "authorization": "Bearer token",
            "service-type": "unbounded",
            "purpose": "unbounded",
        },
        json={},
    )
    assert response.status_code == 422
    # Like chat, invalid input rejected before the dependency runs is unrecorded.
    assert not metrics_spy.samples("search_availability")
    upstream.post.assert_not_awaited()


def test_guest_search_labels(search_boundary, metrics_spy, mocker):
    from mlpa.core.config import env
    from mlpa.core.routers.guest.guest import issue_guest_token

    mocker.patch.object(env, "MLPA_GUEST_ENABLED", True)
    mocker.patch.object(env, "MLPA_GUEST_VIRTUAL_KEY", "test-guest-key")
    client, _, _ = search_boundary
    response = client.post(
        "/v1/search",
        headers={"authorization": f"Bearer {issue_guest_token()}"},
        json={"query": "weather", "max_results": 5},
    )
    assert response.status_code == 200
    _assert_once(
        metrics_spy, AvailabilityReason.VALID_RESPONSE, service_type="search-guest"
    )


@pytest.mark.parametrize("exception", [asyncio.CancelledError(), ClientDisconnect()])
async def test_search_cancellation_records_once(mocker, metrics_spy, exception):
    upstream = AsyncMock()
    upstream.post.side_effect = exception
    mocker.patch("mlpa.core.search.get_http_client", return_value=upstream)
    mocker.patch("mlpa.core.search.redis_service.update_contracts", AsyncMock())
    req = AuthorizedSearchRequest(
        user="u:search", service_type="search", query="weather", max_results=5
    )
    with pytest.raises(type(exception)):
        await get_search(Request({"type": "http", "headers": []}), req)
    _assert_once(metrics_spy, AvailabilityReason.CLIENT_DISCONNECT)


def test_pre_auth_metric_labels_are_bounded(metrics_spy):
    record_search_availability_for(
        AvailabilityReason.INVALID_AUTH_REQUEST,
        service_type="unbounded",
        purpose="unbounded",
        client_country="unbounded",
    )
    _assert_once(
        metrics_spy,
        AvailabilityReason.INVALID_AUTH_REQUEST,
        service_type="other",
        purpose="other",
    )


@pytest.mark.parametrize(
    "country,expected", [("US", "US"), ("BR", "other"), ("", "other")]
)
@pytest.mark.parametrize("auth_rejected", [False, True])
def test_search_country_is_recorded_before_and_after_auth(
    search_boundary, metrics_spy, mocker, country, expected, auth_rejected
):
    from mlpa.core.config import env

    mocker.patch.object(env, "MLPA_LAUNCH_COUNTRIES", {"US"})
    client, _, _ = search_boundary
    if auth_rejected:
        mocker.patch(
            "mlpa.core.auth.authorize.fxa_auth",
            AsyncMock(side_effect=HTTPException(401)),
        )
    response = _post(client, client_country=country)
    assert response.status_code == (401 if auth_rejected else 200)
    _assert_once(
        metrics_spy,
        AvailabilityReason.AUTH_REJECTED
        if auth_rejected
        else AvailabilityReason.VALID_RESPONSE,
        client_country=expected,
    )
