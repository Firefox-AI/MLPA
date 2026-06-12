import json
from unittest.mock import AsyncMock, MagicMock

from mlpa.core.classes import AuthorizedSearchRequest
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
