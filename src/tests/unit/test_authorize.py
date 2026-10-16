import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

from mlpa.core.auth import authorize as authorize_module
from mlpa.core.classes import (
    AuthorizedChatRequest,
    AuthorizedFilterRequest,
    AuthorizedSearchRequest,
    ChatRequest,
    PrivacyFilterRequest,
    SearchRequest,
)
from mlpa.core.config import env


def _make_request(path: str = "/", headers: dict[str, str] | None = None) -> Request:
    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    raw_headers = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request(
        {"type": "http", "method": "POST", "path": path, "headers": raw_headers},
        receive,
    )


async def test_authorize_chat_request_returns_authorized_chat_request(mocker):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_chat_request(
        request=_make_request("/v1/chat/completions"),
        chat_request=ChatRequest(
            model="mistral-small-2603", messages=[{"role": "user", "content": "hello"}]
        ),
        authorization="Bearer token",
        service_type=authorize_module.ServiceType.ai,
        purpose="chat",
    )

    assert isinstance(result, AuthorizedChatRequest)
    assert result.user == "user-123:ai"
    assert result.service_type == "ai"
    assert result.purpose == "chat"
    assert result.messages == [{"role": "user", "content": "hello"}]


async def test_authorize_search_request_returns_authorized_search_request(mocker):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-456"}),
    )

    result = await authorize_module.authorize_search_request(
        request=_make_request("/v1/search/"),
        search_request=SearchRequest(query="latest AI developments", max_results=2),
        authorization="Bearer token",
    )

    assert isinstance(result, AuthorizedSearchRequest)
    assert result.user == "user-456:search"
    assert result.service_type == "search"
    assert result.purpose == ""
    assert result.query == "latest AI developments"
    assert result.max_results == 2


async def test_authorize_filter_request_returns_authorized_filter_request(mocker):
    mocker.patch.object(env, "PRIVACY_FILTER_ENABLED", True)
    fxa_auth_mock = mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-789"}),
    )

    result = await authorize_module.authorize_filter_request(
        request=_make_request("/privacy-filter/"),
        filter_request=PrivacyFilterRequest(items=["email me at jane@example.com"]),
        authorization="Bearer token",
    )

    assert isinstance(result, AuthorizedFilterRequest)
    assert result.user == "user-789"
    assert result.items == ["email me at jane@example.com"]
    fxa_auth_mock.assert_awaited_once_with("Bearer token")


def test_privacy_filter_request_requires_items():
    with pytest.raises(ValidationError) as exc_info:
        PrivacyFilterRequest()

    errors = exc_info.value.errors()
    assert errors[0]["loc"] == ("items",)


async def test_authorize_chat_request_captures_client_country(mocker):
    """AIPLAT-1266: X-Geo-Country lands on AuthorizedChatRequest.client_country."""
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_chat_request(
        request=_make_request("/v1/chat/completions", headers={"X-Geo-Country": "FR"}),
        chat_request=ChatRequest(
            model="mistral-small-2603", messages=[{"role": "user", "content": "hello"}]
        ),
        authorization="Bearer token",
        service_type=authorize_module.ServiceType.ai,
        purpose="chat",
    )

    assert result.client_country == "FR"


async def test_authorize_chat_request_missing_geo_header_defaults_to_empty(mocker):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_chat_request(
        request=_make_request("/v1/chat/completions"),
        chat_request=ChatRequest(
            model="mistral-small-2603", messages=[{"role": "user", "content": "hello"}]
        ),
        authorization="Bearer token",
        service_type=authorize_module.ServiceType.ai,
        purpose="chat",
    )

    assert result.client_country == ""


async def test_authorize_search_request_captures_client_country(mocker):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-456"}),
    )

    result = await authorize_module.authorize_search_request(
        request=_make_request("/v1/search/", headers={"X-Geo-Country": "DE"}),
        search_request=SearchRequest(query="latest AI developments", max_results=2),
        authorization="Bearer token",
    )

    assert result.client_country == "DE"


def test_search_request_rejects_too_many_results():
    with pytest.raises(ValidationError) as exc_info:
        SearchRequest(query="latest AI developments", max_results=11)

    errors = exc_info.value.errors()
    assert errors[0]["loc"] == ("max_results",)


async def test_authorize_chat_request_rejects_invalid_service_type_for_model():
    with pytest.raises(HTTPException) as exc_info:
        await authorize_module.authorize_chat_request(
            request=_make_request("/v1/chat/completions"),
            chat_request=ChatRequest(model="exa", messages=[]),
            authorization="Bearer token",
            service_type=authorize_module.ServiceType.ai,
            purpose="chat",
        )

    assert exc_info.value.status_code == 400
    assert (
        exc_info.value.detail
        == "Invalid service-type value for model exa. Should be one of ['answer', 'sw-answer']"
    )


async def test_authorize_chat_request_rejects_answer_for_non_exa_model():
    with pytest.raises(HTTPException) as exc_info:
        await authorize_module.authorize_chat_request(
            request=_make_request("/v1/chat/completions"),
            chat_request=ChatRequest(model="mistral-small-2603", messages=[]),
            authorization="Bearer token",
            service_type=authorize_module.ServiceType.answer,
            purpose=None,
        )

    assert exc_info.value.status_code == 400
    assert (
        exc_info.value.detail
        == "Invalid service-type value answer for model mistral-small-2603. "
        "Service type answer is only valid for models ['exa']"
    )


async def test_authorize_chat_request_accepts_sw_answer_for_exa_model(mocker):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_chat_request(
        request=_make_request("/v1/chat/completions"),
        chat_request=ChatRequest(model="exa", messages=[]),
        authorization="Bearer token",
        service_type=authorize_module.ServiceType("sw-answer"),
        purpose=None,
    )

    assert isinstance(result, AuthorizedChatRequest)
    assert result.user == "user-123:sw-answer"
    assert result.service_type == "sw-answer"
    assert result.purpose == ""


async def test_authorize_chat_request_rejects_sw_answer_for_non_exa_model():
    with pytest.raises(HTTPException) as exc_info:
        await authorize_module.authorize_chat_request(
            request=_make_request("/v1/chat/completions"),
            chat_request=ChatRequest(model="mistral-small-2603", messages=[]),
            authorization="Bearer token",
            service_type=authorize_module.ServiceType("sw-answer"),
            purpose=None,
        )

    assert exc_info.value.status_code == 400
    assert (
        exc_info.value.detail
        == "Invalid service-type value sw-answer for model mistral-small-2603. "
        "Service type sw-answer is only valid for models ['exa']"
    )


async def test_authorize_chat_request_rejects_invalid_model_name(mocker):
    valid_service_type_mock = mocker.patch.object(
        type(authorize_module.env), "valid_service_type_for_model"
    )
    fxa_auth_mock = mocker.patch.object(authorize_module, "fxa_auth")

    with pytest.raises(HTTPException) as exc_info:
        await authorize_module.authorize_chat_request(
            request=_make_request("/v1/chat/completions"),
            chat_request=ChatRequest(
                model="' OR (SELECT pg_sleep(6)) IS NULL --", messages=[]
            ),
            authorization="Bearer token",
            service_type=authorize_module.ServiceType.ai,
            purpose="chat",
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {"error": 8}
    valid_service_type_mock.assert_not_called()
    fxa_auth_mock.assert_not_called()


@pytest.fixture
def guest_enabled(mocker):
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", True)
    mocker.patch.object(env, "MLPA_GUEST_VIRTUAL_KEY", "sk-guest")


def _guest_chat_kwargs(authorization: str, service_type="ai", purpose="chat"):
    return dict(
        request=_make_request("/v1/chat/completions"),
        chat_request=ChatRequest(
            model="mistral-small-2603", messages=[{"role": "user", "content": "hi"}]
        ),
        authorization=authorization,
        service_type=authorize_module.ServiceType(service_type),
        purpose=purpose,
    )


async def test_guest_token_gets_guest_service_type_and_key(guest_enabled, mocker):
    import jwt

    from mlpa.core.routers.guest.guest import issue_guest_token

    fxa_auth = mocker.patch.object(authorize_module, "fxa_auth", mocker.AsyncMock())
    token = issue_guest_token()
    sub = jwt.decode(token, options={"verify_signature": False})["sub"]
    kwargs = _guest_chat_kwargs(f"Bearer {token}")

    result = await authorize_module.authorize_chat_request(**kwargs)

    assert result.user == f"{sub}:ai-guest"
    assert result.service_type == "ai-guest"
    assert result.litellm_virtual_key == "sk-guest"
    assert result.purpose == "chat"
    assert kwargs["request"].state.service_type == "ai-guest"
    fxa_auth.assert_not_called()


async def test_forged_guest_token_is_rejected(guest_enabled, mocker):
    import jwt

    from mlpa.core.routers.guest.guest import GUEST_TOKEN_HEADERS, GUEST_TOKEN_ISSUER

    forged = jwt.encode(
        {"iss": GUEST_TOKEN_ISSUER, "aud": "mlpa", "sub": "x", "iat": 1, "exp": 2**40},
        "wrong-secret",
        algorithm="HS256",
        headers=GUEST_TOKEN_HEADERS,
    )
    with pytest.raises(HTTPException) as exc:
        await authorize_module.authorize_chat_request(
            **_guest_chat_kwargs(f"Bearer {forged}")
        )
    assert exc.value.status_code == 401


async def test_guest_token_rejected_while_guests_disabled(mocker):
    from mlpa.core.routers.guest.guest import issue_guest_token

    mocker.patch.object(env, "MLPA_GUEST_ENABLED", False)
    with pytest.raises(HTTPException) as exc:
        await authorize_module.authorize_chat_request(
            **_guest_chat_kwargs(f"Bearer {issue_guest_token()}")
        )
    assert exc.value.status_code == 401


async def test_guest_token_on_service_type_without_guest_variant(guest_enabled):
    from mlpa.core.routers.guest.guest import issue_guest_token

    with pytest.raises(HTTPException) as exc:
        await authorize_module.authorize_chat_request(
            **_guest_chat_kwargs(
                f"Bearer {issue_guest_token()}", service_type="s2s", purpose=None
            )
        )
    assert exc.value.status_code == 400


def test_clients_cannot_send_guest_service_types():
    with pytest.raises(ValueError):
        authorize_module.ServiceType("ai-guest")


async def test_guest_token_on_search_gets_search_guest(guest_enabled):
    import jwt

    from mlpa.core.routers.guest.guest import issue_guest_token

    token = issue_guest_token()
    sub = jwt.decode(token, options={"verify_signature": False})["sub"]

    result = await authorize_module.authorize_search_request(
        request=_make_request("/v1/search/"),
        search_request=SearchRequest(query="q", max_results=2),
        authorization=f"Bearer {token}",
    )

    assert result.user == f"{sub}:search-guest"
    assert result.service_type == "search-guest"
    assert result.litellm_virtual_key == "sk-guest"
