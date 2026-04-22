from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException

from mlpa.core.classes import AuthorizedRequestContext
from mlpa.core.config import LITELLM_COMPLETION_AUTH_HEADERS, LITELLM_EXA_SEARCH_URL
from mlpa.core.search import proxy_exa_search


async def test_proxy_exa_search_success(mocker):
    authorized_request = AuthorizedRequestContext(
        user="test-user-123:ai",
        service_type="ai",
        purpose="chat",
    )
    mock_response = MagicMock()
    mock_response.json.return_value = {"results": [{"title": "Example"}]}
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.search.get_http_client", return_value=mock_client)

    body = {"query": "latest AI developments", "max_results": 5}
    result = await proxy_exa_search(authorized_request, body)

    mock_client.post.assert_awaited_once_with(
        LITELLM_EXA_SEARCH_URL,
        headers=LITELLM_COMPLETION_AUTH_HEADERS,
        json={
            "query": "latest AI developments",
            "max_results": 5,
            "user": "test-user-123:ai",
        },
    )
    assert result == {"results": [{"title": "Example"}]}


async def test_proxy_exa_search_http_error(mocker):
    authorized_request = AuthorizedRequestContext(
        user="test-user-123:ai",
        service_type="ai",
        purpose="chat",
    )
    mock_response = MagicMock()
    mock_response.text = "bad request"
    mock_response.status_code = 400

    mock_http_error = httpx.HTTPStatusError(
        "bad request", request=MagicMock(), response=mock_response
    )
    mock_response.raise_for_status.side_effect = mock_http_error

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response
    mocker.patch("mlpa.core.search.get_http_client", return_value=mock_client)

    with pytest.raises(HTTPException) as exc_info:
        await proxy_exa_search(authorized_request, {"query": "bad"})

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == {"error": "Upstream service returned an error"}
