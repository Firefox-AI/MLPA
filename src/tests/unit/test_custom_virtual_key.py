"""
Custom LiteLLM virtual key passthrough (`litellm-virtual-key` header).

The header lets a caller select a LiteLLM key carrying its own budget /
rate-limit / model configuration instead of the shared MLPA_VIRTUAL_KEY.  The header
only takes effect while env.ALLOW_CUSTOM_VIRTUAL_KEY is true
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request

import mlpa.run as run_module
from mlpa.core.auth import authorize as authorize_module
from mlpa.core.classes import (
    AuthorizedChatRequest,
    AuthorizedSearchRequest,
    ChatRequest,
    SearchRequest,
)
from mlpa.core.completions import _build_litellm_body, get_completion
from mlpa.core.config import (
    LITELLM_VIRTUAL_AUTH_HEADERS,
    Env,
    env,
    litellm_virtual_auth_headers,
)
from mlpa.core.search import get_search
from tests.consts import SUCCESSFUL_CHAT_RESPONSE

CUSTOM_KEY = "sk-custom-config"
UNKNOWN_KEY = "sk-not-provisioned"


@pytest.fixture
def allow_custom_virtual_key(mocker):
    mocker.patch.object(env, "ALLOW_CUSTOM_VIRTUAL_KEY", True)


def _make_request(headers: dict[str, str] | None = None):
    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": raw_headers},
        receive,
    )


def test_no_key_reuses_the_shared_default_headers():
    assert litellm_virtual_auth_headers(None) is LITELLM_VIRTUAL_AUTH_HEADERS


def test_custom_key_becomes_the_bearer_token():
    assert litellm_virtual_auth_headers(CUSTOM_KEY) == {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CUSTOM_KEY}",
    }


def test_key_outside_the_allowlist_raises():
    with pytest.raises(ValueError) as excinfo:
        litellm_virtual_auth_headers(UNKNOWN_KEY)
    assert UNKNOWN_KEY not in str(excinfo.value)


def test_header_ignored_while_feature_disabled(mocker):
    mocker.patch.object(env, "ALLOW_CUSTOM_VIRTUAL_KEY", False)
    assert authorize_module._resolve_custom_virtual_key(CUSTOM_KEY) is None


@pytest.mark.parametrize("header", [None, "", "   "])
def test_absent_or_blank_header_falls_back_to_default(allow_custom_virtual_key, header):
    assert authorize_module._resolve_custom_virtual_key(header) is None


def test_unknown_key_is_rejected_with_400(allow_custom_virtual_key):
    with pytest.raises(HTTPException) as excinfo:
        authorize_module._resolve_custom_virtual_key(UNKNOWN_KEY)
    assert excinfo.value.status_code == 400
    assert UNKNOWN_KEY not in str(excinfo.value.detail)


async def test_unknown_key_rejected_before_the_response_starts(
    allow_custom_virtual_key, mocker
):
    """
    The authorize dependency must reject, not the proxy: on the streaming path
    `litellm_virtual_auth_headers` runs inside the response generator, where a
    raise can no longer set a status code.
    """
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    with pytest.raises(HTTPException) as excinfo:
        await authorize_module.authorize_chat_request(
            request=_make_request(),
            chat_request=ChatRequest(
                model="gpt-oss-120b",
                messages=[{"role": "user", "content": "hello"}],
                stream=True,
            ),
            authorization="Bearer token",
            service_type=authorize_module.ServiceType.ai,
            purpose="chat",
            litellm_virtual_key=UNKNOWN_KEY,
        )

    assert excinfo.value.status_code == 400


def test_unknown_key_ignored_rather_than_rejected_while_disabled(mocker):
    """Flag off short-circuits before the allowlist, so no 400 leaks its state."""
    mocker.patch.object(env, "ALLOW_CUSTOM_VIRTUAL_KEY", False)
    assert authorize_module._resolve_custom_virtual_key(UNKNOWN_KEY) is None


def test_key_is_stripped_when_enabled(allow_custom_virtual_key):
    assert authorize_module._resolve_custom_virtual_key(f"  {CUSTOM_KEY}  ") == (
        CUSTOM_KEY
    )


async def test_authorize_chat_request_carries_the_key(allow_custom_virtual_key, mocker):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_chat_request(
        request=_make_request(),
        chat_request=ChatRequest(
            model="gpt-oss-120b", messages=[{"role": "user", "content": "hello"}]
        ),
        authorization="Bearer token",
        service_type=authorize_module.ServiceType.ai,
        purpose="chat",
        litellm_virtual_key=CUSTOM_KEY,
    )

    assert result.litellm_virtual_key == CUSTOM_KEY


async def test_authorize_search_request_carries_the_key(
    allow_custom_virtual_key, mocker
):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_search_request(
        request=_make_request(),
        search_request=SearchRequest(query="q", max_results=3),
        authorization="Bearer token",
        litellm_virtual_key=CUSTOM_KEY,
    )

    assert result.litellm_virtual_key == CUSTOM_KEY


async def test_authorize_chat_request_drops_key_when_disabled(mocker):
    mocker.patch.object(env, "ALLOW_CUSTOM_VIRTUAL_KEY", False)
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_chat_request(
        request=_make_request(),
        chat_request=ChatRequest(
            model="gpt-oss-120b", messages=[{"role": "user", "content": "hello"}]
        ),
        authorization="Bearer token",
        service_type=authorize_module.ServiceType.ai,
        purpose="chat",
        litellm_virtual_key=CUSTOM_KEY,
    )

    assert result.litellm_virtual_key is None


# --- the proxy call sites --------------------------------------------------


def _chat_request(**overrides) -> AuthorizedChatRequest:
    return AuthorizedChatRequest(
        user="test-user-123:ai",
        service_type="ai",
        purpose="chat",
        model="test-model",
        messages=[{"role": "user", "content": "Hello"}],
        **overrides,
    )


@pytest.mark.parametrize(
    "key, expected_auth",
    [
        (CUSTOM_KEY, f"Bearer {CUSTOM_KEY}"),
        (None, LITELLM_VIRTUAL_AUTH_HEADERS["Authorization"]),
    ],
)
async def test_non_stream_completion_uses_expected_auth(
    mocker, metrics_spy, key, expected_auth
):
    mock_response = MagicMock()
    mock_response.json.return_value = SUCCESSFUL_CHAT_RESPONSE
    mock_response.headers = {}
    mock_response.raise_for_status.return_value = None
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=mock_client)

    await get_completion(_chat_request(litellm_virtual_key=key))

    _, call_kwargs = mock_client.post.call_args
    assert call_kwargs["headers"]["Authorization"] == expected_auth
    assert call_kwargs["headers"]["Content-Type"] == "application/json"


async def test_search_uses_the_custom_key(mocker):
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_response.headers = {}
    mock_response.raise_for_status.return_value = None
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.search.get_http_client", return_value=mock_client)

    await get_search(
        AuthorizedSearchRequest(
            user="test-user-123:search",
            service_type="search",
            query="q",
            max_results=3,
            litellm_virtual_key=CUSTOM_KEY,
        )
    )

    _, call_kwargs = mock_client.post.call_args
    assert call_kwargs["headers"]["Authorization"] == f"Bearer {CUSTOM_KEY}"


# --- the key must not leak ------------------------------------------------


def test_key_is_not_forwarded_in_the_chat_body():
    body = _build_litellm_body(
        _chat_request(litellm_virtual_key=CUSTOM_KEY), stream=False
    )
    assert "litellm_virtual_key" not in body
    assert CUSTOM_KEY not in str(body)


async def test_key_is_not_forwarded_in_the_search_body(mocker):
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": []}
    mock_response.headers = {}
    mock_response.raise_for_status.return_value = None
    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.search.get_http_client", return_value=mock_client)

    await get_search(
        AuthorizedSearchRequest(
            user="test-user-123:search",
            service_type="search",
            query="q",
            max_results=3,
            litellm_virtual_key=CUSTOM_KEY,
        )
    )

    _, call_kwargs = mock_client.post.call_args
    assert "litellm_virtual_key" not in call_kwargs["json"]


def test_key_is_not_in_the_log_fields():
    fields = _chat_request(litellm_virtual_key=CUSTOM_KEY).log_fields
    assert CUSTOM_KEY not in str(fields)


def test_key_header_is_scrubbed_from_sentry_events():
    event = {
        "request": {
            "headers": {"Litellm-Virtual-Key": CUSTOM_KEY, "Accept": "*/*"},
        }
    }
    scrubbed = run_module.sentry_scrub_sensitive_fields(event, None)
    assert scrubbed["request"]["headers"]["Litellm-Virtual-Key"] == "[Filtered]"
    assert scrubbed["request"]["headers"]["Accept"] == "*/*"
