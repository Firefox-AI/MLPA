"""
Custom LiteLLM virtual key passthrough (`litellm-virtual-key` header).

The header lets a caller select a LiteLLM key carrying its own budget /
rate-limit / model configuration instead of the shared MLPA_VIRTUAL_KEY.  The header
only takes effect while env.ALLOW_CUSTOM_VIRTUAL_KEY is true
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException, Request
from pytest_httpx import IteratorStream

import mlpa.run as run_module
from mlpa.core.auth import authorize as authorize_module
from mlpa.core.classes import (
    AuthorizedChatRequest,
    AuthorizedSearchRequest,
    ChatRequest,
    SearchRequest,
)
from mlpa.core.completions import _build_litellm_body, get_completion, stream_completion
from mlpa.core.config import (
    Env,
    VirtualKeyConfigurationError,
    env,
    resolve_litellm_virtual_auth_headers,
    resolve_litellm_virtual_key,
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


def test_no_key_uses_the_service_default(mocker):
    mocker.patch.object(env, "MLPA_VIRTUAL_KEY", "sk-default-test")
    assert resolve_litellm_virtual_auth_headers("ai") == {
        "Content-Type": "application/json",
        "Authorization": "Bearer sk-default-test",
    }


def test_custom_key_becomes_the_bearer_token():
    assert resolve_litellm_virtual_auth_headers("ai", CUSTOM_KEY) == {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {CUSTOM_KEY}",
    }


def test_service_key_map_covers_all_types_with_separate_guest_defaults(mocker):
    mocker.patch.object(env, "MLPA_VIRTUAL_KEY", "sk-signed-in")
    mocker.patch.object(env, "MLPA_GUEST_VIRTUAL_KEY", "sk-guest")
    mocker.patch.object(env, "MLPA_SERVICE_TYPE_VIRTUAL_KEYS", {})
    assert set(env.service_type_virtual_keys) == set(env.valid_service_types)
    for service_type, key in env.service_type_virtual_keys.items():
        assert key == (
            "sk-guest" if service_type.endswith("-guest") else "sk-signed-in"
        )


def test_service_key_overrides_load_from_environment(monkeypatch):
    monkeypatch.setenv(
        "MLPA_SERVICE_TYPE_VIRTUAL_KEYS",
        '{"widget-generation": "sk-widget", "search-guest": "sk-guest-search"}',
    )
    settings = Env()
    assert settings.service_type_virtual_keys["widget-generation"] == "sk-widget"
    assert settings.service_type_virtual_keys["search-guest"] == "sk-guest-search"
    assert settings.service_type_virtual_keys["ai"] == settings.MLPA_VIRTUAL_KEY


@pytest.mark.parametrize(
    "service_type", ["widget-generation", "search", "ai-guest", "search-guest"]
)
def test_resolver_uses_service_key_override(mocker, service_type):
    mocker.patch.object(
        env, "MLPA_SERVICE_TYPE_VIRTUAL_KEYS", {service_type: "sk-service"}
    )
    assert (
        resolve_litellm_virtual_auth_headers(service_type)["Authorization"]
        == "Bearer sk-service"
    )


def test_custom_key_takes_precedence_over_signed_in_service_key(mocker):
    mocker.patch.object(env, "MLPA_SERVICE_TYPE_VIRTUAL_KEYS", {"ai": "sk-service"})
    assert resolve_litellm_virtual_key("ai", CUSTOM_KEY) == CUSTOM_KEY


def test_guest_ignores_custom_key_and_uses_service_override(mocker):
    mocker.patch.object(
        env, "MLPA_SERVICE_TYPE_VIRTUAL_KEYS", {"ai-guest": "sk-service-guest"}
    )
    assert resolve_litellm_virtual_key("ai-guest", CUSTOM_KEY) == "sk-service-guest"


def test_guest_without_key_rejects_custom_key_instead_of_falling_back(mocker):
    mocker.patch.object(env, "MLPA_GUEST_VIRTUAL_KEY", None)
    mocker.patch.object(env, "MLPA_SERVICE_TYPE_VIRTUAL_KEYS", {})
    with pytest.raises(VirtualKeyConfigurationError) as exc:
        resolve_litellm_virtual_auth_headers("ai-guest", CUSTOM_KEY)
    assert "access is not configured." in str(exc.value)


def test_explicitly_unconfigured_service_is_unavailable(mocker):
    mocker.patch.object(
        env, "MLPA_SERVICE_TYPE_VIRTUAL_KEYS", {"widget-generation": None}
    )
    with pytest.raises(VirtualKeyConfigurationError) as exc:
        resolve_litellm_virtual_auth_headers("widget-generation")
    assert "access is not configured." in str(exc.value)


def test_header_ignored_while_feature_disabled(mocker):
    mocker.patch.object(env, "ALLOW_CUSTOM_VIRTUAL_KEY", False)
    assert authorize_module._resolve_custom_virtual_key(CUSTOM_KEY) is None


@pytest.mark.parametrize("header", [None, "", "   "])
def test_absent_or_blank_header_falls_back_to_default(allow_custom_virtual_key, header):
    assert authorize_module._resolve_custom_virtual_key(header) is None


def test_unknown_key_ignored_rather_than_rejected_while_disabled(mocker):
    """Flag off short-circuits before the allowlist, so no 400 leaks its state."""
    mocker.patch.object(env, "ALLOW_CUSTOM_VIRTUAL_KEY", False)
    assert authorize_module._resolve_custom_virtual_key(UNKNOWN_KEY) is None


def test_key_is_stripped_when_enabled(allow_custom_virtual_key):
    assert authorize_module._resolve_custom_virtual_key(f"  {CUSTOM_KEY}  ") == (
        CUSTOM_KEY
    )


def test_guest_virtual_key_returns_configured_key(mocker):
    mocker.patch.object(env, "MLPA_GUEST_VIRTUAL_KEY", "sk-guest")
    assert resolve_litellm_virtual_key("ai-guest") == "sk-guest"


@pytest.mark.parametrize("key", [None, ""])
def test_guest_virtual_key_unset_is_rejected(mocker, key):
    """Never falls back to MLPA_VIRTUAL_KEY, that would bill guests to FxA users."""
    mocker.patch.object(env, "MLPA_GUEST_VIRTUAL_KEY", key)
    with pytest.raises(VirtualKeyConfigurationError) as exc:
        resolve_litellm_virtual_key("ai-guest")
    assert "access is not configured." in str(exc.value)


async def test_authorize_chat_request_carries_the_key(allow_custom_virtual_key, mocker):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_chat_request(
        request=_make_request(),
        chat_request=ChatRequest(
            model="mistral-small-2603", messages=[{"role": "user", "content": "hello"}]
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
            model="mistral-small-2603", messages=[{"role": "user", "content": "hello"}]
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
        (None, f"Bearer {env.MLPA_VIRTUAL_KEY}"),
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

    await get_completion(_make_request(), _chat_request(litellm_virtual_key=key))

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
        _make_request(),
        AuthorizedSearchRequest(
            user="test-user-123:search",
            service_type="search",
            query="q",
            max_results=3,
            litellm_virtual_key=CUSTOM_KEY,
        ),
    )

    _, call_kwargs = mock_client.post.call_args
    assert call_kwargs["headers"]["Authorization"] == f"Bearer {CUSTOM_KEY}"


@pytest.mark.parametrize("endpoint", ["chat", "stream", "search"])
@pytest.mark.parametrize("custom_key", [None, CUSTOM_KEY])
async def test_proxy_uses_service_key_with_custom_override_precedence(
    mocker, httpx_mock, mock_request, metrics_spy, endpoint, custom_key
):
    service_type = "search" if endpoint == "search" else "widget-generation"
    mocker.patch.object(
        env, "MLPA_SERVICE_TYPE_VIRTUAL_KEYS", {service_type: "sk-service"}
    )
    if endpoint == "stream":
        httpx_mock.add_response(stream=IteratorStream([b"data: [DONE]\n\n"]))
    else:
        httpx_mock.add_response(
            json={"results": []} if endpoint == "search" else SUCCESSFUL_CHAT_RESPONSE
        )
    if endpoint == "search":
        await get_search(
            mock_request,
            AuthorizedSearchRequest(
                user="test-user:search",
                service_type="search",
                query="q",
                max_results=3,
                litellm_virtual_key=custom_key,
            ),
        )
    else:
        chat = _chat_request(litellm_virtual_key=custom_key).model_copy(
            update={"service_type": service_type, "user": f"test-user:{service_type}"}
        )
        if endpoint == "stream":
            chunks = [chunk async for chunk in stream_completion(mock_request, chat)]
            assert chunks
        else:
            await get_completion(mock_request, chat)
    upstream_request = httpx_mock.get_request()
    assert (
        upstream_request.headers["Authorization"]
        == f"Bearer {custom_key or 'sk-service'}"
    )
    assert b"sk-service" not in upstream_request.content
    assert CUSTOM_KEY.encode() not in upstream_request.content


async def test_guest_authorization_uses_service_override_and_ignores_custom_header(
    allow_custom_virtual_key, mocker
):
    from mlpa.core.routers.guest.guest import issue_guest_token

    mocker.patch.object(env, "MLPA_GUEST_ENABLED", True)
    mocker.patch.object(env, "MLPA_GUEST_VIRTUAL_KEY", None)
    mocker.patch.object(
        env, "MLPA_SERVICE_TYPE_VIRTUAL_KEYS", {"search-guest": "sk-search-guest"}
    )
    authorized = await authorize_module.authorize_search_request(
        request=_make_request(),
        search_request=SearchRequest(query="q", max_results=3),
        authorization=f"Bearer {issue_guest_token()}",
        litellm_virtual_key=CUSTOM_KEY,
    )
    assert authorized.service_type == "search-guest"
    assert authorized.litellm_virtual_key == "sk-search-guest"


@pytest.mark.parametrize("endpoint", ["chat", "stream", "search"])
async def test_proxy_translates_missing_key_to_503(
    mocker, mock_request, metrics_spy, endpoint
):
    mocker.patch.object(
        env, "MLPA_SERVICE_TYPE_VIRTUAL_KEYS", {"ai": None, "search": None}
    )
    upstream = mocker.AsyncMock()
    mocker.patch("mlpa.core.completions.get_http_client", return_value=upstream)
    mocker.patch("mlpa.core.search.get_http_client", return_value=upstream)
    with pytest.raises(HTTPException) as exc:
        if endpoint == "search":
            await get_search(
                mock_request,
                AuthorizedSearchRequest(
                    user="test-user:search",
                    service_type="search",
                    query="q",
                    max_results=3,
                ),
            )
        elif endpoint == "stream":
            async for _ in stream_completion(mock_request, _chat_request()):
                pass
        else:
            await get_completion(mock_request, _chat_request())
    assert exc.value.status_code == 503
    assert exc.value.detail == {"error": "Service access is not configured."}
    upstream.post.assert_not_called()
    upstream.stream.assert_not_called()


@pytest.mark.parametrize("guest", [False, True])
@pytest.mark.parametrize("endpoint", ["chat", "search"])
async def test_authorization_translates_missing_key_before_proxying(
    mocker, guest, endpoint
):
    from mlpa.core.routers.guest.guest import issue_guest_token

    mocker.patch.object(env, "MLPA_GUEST_ENABLED", True)
    mocker.patch.object(
        env,
        "MLPA_SERVICE_TYPE_VIRTUAL_KEYS",
        {"ai": None, "search": None, "ai-guest": None, "search-guest": None},
    )
    mocker.patch.object(
        authorize_module, "fxa_auth", return_value={"user": "test-user"}
    )
    authorization = f"Bearer {issue_guest_token()}" if guest else "Bearer test-token"
    with pytest.raises(HTTPException) as exc:
        if endpoint == "search":
            await authorize_module.authorize_search_request(
                request=_make_request(),
                search_request=SearchRequest(query="q", max_results=3),
                authorization=authorization,
            )
        else:
            await authorize_module.authorize_chat_request(
                request=_make_request(),
                chat_request=ChatRequest(
                    model="mistral-small-2603", messages=[], stream=True
                ),
                authorization=authorization,
                service_type=authorize_module.ServiceType.ai,
            )
    assert exc.value.status_code == 503
    assert exc.value.detail == {
        "error": "Guest access is not configured."
        if guest
        else "Service access is not configured."
    }


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
        _make_request(),
        AuthorizedSearchRequest(
            user="test-user-123:search",
            service_type="search",
            query="q",
            max_results=3,
            litellm_virtual_key=CUSTOM_KEY,
        ),
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
