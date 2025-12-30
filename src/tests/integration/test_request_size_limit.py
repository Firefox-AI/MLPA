import json

from mlpa.core.config import env
from tests.consts import TEST_FXA_TOKEN


def test_request_size_under_limit(mocked_client_integration):
    """Test that requests under the size limit pass through."""
    small_payload = {
        "messages": [{"role": "user", "content": "Hello"}],
        "model": "test-model",
    }

    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
        },
        json=small_payload,
    )
    assert response.status_code != 413


def test_request_size_over_limit(mocked_client_integration):
    """Test that requests over the size limit return 413."""
    max_size = env.MAX_REQUEST_SIZE_BYTES
    oversized_size = max_size + 1

    large_content = "x" * (max_size // 2)
    large_payload = {
        "messages": [{"role": "user", "content": large_content}],
        "model": "test-model",
    }

    payload_json = json.dumps(large_payload)
    payload_size = len(payload_json.encode("utf-8"))

    if payload_size <= max_size:
        response = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
                "content-length": str(oversized_size),
            },
            json=large_payload,
        )
    else:
        response = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
            },
            json=large_payload,
        )

    assert response.status_code == 413
    assert "error" in response.json()
    assert "too large" in response.json()["error"].lower()


def test_request_size_exactly_at_limit(mocked_client_integration):
    """Test that requests exactly at the limit pass through."""
    max_size = env.MAX_REQUEST_SIZE_BYTES

    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
            "content-length": str(max_size),
        },
        json={"messages": [{"role": "user", "content": "test"}]},
    )
    assert response.status_code != 413


def test_request_size_limit_only_applies_to_completions_endpoint(
    mocked_client_integration,
):
    """Test that size limit only applies to /v1/chat/completions endpoint."""
    max_size = env.MAX_REQUEST_SIZE_BYTES
    oversized_size = max_size + 1

    response = mocked_client_integration.post(
        "/health/liveness",
        headers={
            "content-length": str(oversized_size),
        },
        json={"test": "data"},
    )
    assert response.status_code != 413


def test_request_size_limit_with_invalid_content_length(mocked_client_integration):
    """Test that invalid Content-Length header doesn't break the middleware."""
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
            "content-length": "invalid",
        },
        json={"messages": [{"role": "user", "content": "test"}]},
    )
    assert response.status_code != 413
