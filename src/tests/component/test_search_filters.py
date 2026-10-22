import httpx
import pytest

from mlpa.core.config import LITELLM_SEARCH_URL
from tests.consts import TEST_FXA_TOKEN

FILTERS = {
    "includeDomains": ["mozilla.org", "developer.mozilla.org"],
    "excludeDomains": ["example.org"],
    "startPublishedDate": "2026-01-01T00:00:00Z",
    "category": "news",
}
HEADERS = {
    "authorization": f"Bearer {TEST_FXA_TOKEN}",
    "service-type": "search",
}


@pytest.mark.parametrize(
    "filters",
    [
        {},
        *({key: value} for key, value in FILTERS.items()),
        FILTERS,
        {key: None for key in FILTERS},
        {"includeDomains": [], "excludeDomains": []},
    ],
)
def test_search_filters_reach_litellm(mocked_client_integration, mocker, filters):
    """Exercise request validation, authorization, and upstream serialization."""
    upstream_url = f"{LITELLM_SEARCH_URL}/exa-search"
    upstream = mocker.AsyncMock()
    upstream.post.return_value = httpx.Response(
        200,
        json={"results": []},
        request=httpx.Request("POST", upstream_url),
    )
    mocker.patch("mlpa.core.search.get_http_client", return_value=upstream)

    response = mocked_client_integration.post(
        "/v1/search",
        headers=HEADERS,
        json={"query": "browser news", "max_results": 5, **filters},
    )

    assert response.status_code == 200
    assert response.json() == {"results": []}
    upstream.post.assert_awaited_once()
    args, kwargs = upstream.post.call_args
    assert args == (upstream_url,)
    body = kwargs["json"]
    assert body.pop("user").endswith(":search")
    assert body == {
        "query": "browser news",
        "max_results": 5,
        **{key: value for key, value in filters.items() if value is not None},
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("includeDomains", "mozilla.org"),
        ("excludeDomains", "example.org"),
        ("includeDomains", [123]),
        ("excludeDomains", [123]),
        ("startPublishedDate", ["2026-01-01T00:00:00Z"]),
        ("category", ["news"]),
    ],
)
def test_search_rejects_invalid_filter_types(
    mocked_client_integration, mocker, field, value
):
    get_search = mocker.patch("mlpa.run.get_search", new_callable=mocker.AsyncMock)

    response = mocked_client_integration.post(
        "/v1/search",
        headers=HEADERS,
        json={"query": "browser news", "max_results": 5, field: value},
    )

    assert response.status_code == 422
    assert any(error["loc"][1] == field for error in response.json()["detail"])
    get_search.assert_not_awaited()
