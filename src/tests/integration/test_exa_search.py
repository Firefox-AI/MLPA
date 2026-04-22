from unittest.mock import AsyncMock, patch

from tests.consts import TEST_FXA_TOKEN


def test_exa_search_success(mocked_client_integration):
    with (
        patch(
            "mlpa.run.get_or_create_user",
            new=AsyncMock(return_value=({"blocked": False}, False)),
        ),
        patch("mlpa.run.proxy_exa_search", new=AsyncMock(return_value={"results": []})),
    ):
        response = mocked_client_integration.post(
            "/v1/search/exa-search",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
                "purpose": "chat",
            },
            json={"query": "latest AI developments", "max_results": 5},
        )

    assert response.status_code == 200
    assert response.json() == {"results": []}


def test_exa_search_missing_auth(mocked_client_integration):
    response = mocked_client_integration.post(
        "/v1/search/exa-search",
        headers={
            "service-type": "ai",
            "purpose": "chat",
        },
        json={"query": "latest AI developments"},
    )

    assert response.status_code == 422


def test_exa_search_invalid_json_shape(mocked_client_integration):
    response = mocked_client_integration.post(
        "/v1/search/exa-search",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
            "purpose": "chat",
        },
        json=["not", "an", "object"],
    )

    assert response.status_code == 400
    assert response.json() == {"detail": {"error": "JSON body must be an object"}}
