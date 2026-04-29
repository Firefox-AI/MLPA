import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

from mlpa.core.auth import authorize as authorize_module
from mlpa.core.classes import (
    AuthorizedChatRequest,
    AuthorizedSearchRequest,
    ChatRequest,
    SearchRequest,
)


def _make_request(path: str = "/") -> Request:
    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": path, "headers": []}, receive
    )


async def test_authorize_chat_request_returns_authorized_chat_request(mocker):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_chat_request(
        request=_make_request("/v1/chat/completions"),
        chat_request=ChatRequest(messages=[{"role": "user", "content": "hello"}]),
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
        == "Invalid service-type value for model exa. Should be one of ['search']"
    )
