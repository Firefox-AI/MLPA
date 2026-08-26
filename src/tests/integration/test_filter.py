import json

from mlpa.core.config import (
    PRIVACY_FILTER_FILTER_URL,
    PRIVACY_FILTER_MASTER_AUTH_HEADERS,
)
from tests.consts import TEST_FXA_TOKEN


def test_filter_forwards_items_to_privacy_filter(mocked_client_integration, httpx_mock):
    upstream_response = {
        "results": [
            {
                "masked_text": "email me at [EMAIL]",
                "spans": [
                    {
                        "category": "email",
                        "text": "jane@example.com",
                        "start": 12,
                        "end": 28,
                        "score": 0.99,
                    }
                ],
            }
        ],
        "model_id": "pii-filter",
        "num_items": 1,
    }
    httpx_mock.add_response(
        method="POST",
        url=PRIVACY_FILTER_FILTER_URL,
        status_code=200,
        json=upstream_response,
    )

    response = mocked_client_integration.post(
        "/filter/",
        headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
        json={"items": ["email me at jane@example.com"]},
    )

    assert response.status_code == 200
    assert response.json() == upstream_response

    request = httpx_mock.get_request()
    assert request is not None
    request_body = json.loads(request.content)
    assert request_body == {"items": ["email me at jane@example.com"]}
    assert "user" not in request_body
    assert (
        request.headers["x-pf-api-key"]
        == PRIVACY_FILTER_MASTER_AUTH_HEADERS["x-pf-api-key"]
    )


def test_filter_rejects_missing_items(mocked_client_integration):
    response = mocked_client_integration.post(
        "/filter/",
        headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
        json={},
    )

    assert response.status_code == 422


def test_filter_rejects_invalid_fxa_auth(mocked_client_integration):
    response = mocked_client_integration.post(
        "/filter/",
        headers={"authorization": f"Bearer {TEST_FXA_TOKEN}invalid"},
        json={"items": ["email me at jane@example.com"]},
    )

    assert response.status_code == 401
