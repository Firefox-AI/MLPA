from unittest.mock import patch

from tests.consts import SUCCESSFUL_CHAT_RESPONSE, TEST_FXA_TOKEN

DEV_TOKEN = "dev-experimentation-secret-token"


def test_missing_auth(mocked_client_integration):
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        json={},
    )
    assert response.status_code == 422


def test_missing_service_type(mocked_client_integration):
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={"authorization": f"Bearer {TEST_FXA_TOKEN}"},
        json={},
    )
    assert response.status_code == 422


def test_invalid_fxa_auth(mocked_client_integration):
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": "Bearer " + TEST_FXA_TOKEN + "invalid",
            "service-type": "ai",
        },
        json={},
    )
    assert response.status_code == 401


def test_successful_request_with_mocked_fxa_auth(mocked_client_integration):
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={"authorization": "Bearer " + TEST_FXA_TOKEN, "service-type": "ai"},
        json={},
    )
    assert response.status_code != 401
    assert response.status_code != 400
    assert response.json() == SUCCESSFUL_CHAT_RESPONSE


def test_x_dev_authorization_success(mocked_client_integration):
    """Test that x-dev-authorization + FxA Authorization authorizes the request."""
    with patch(
        "mlpa.core.auth.authorize.env.MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN",
        DEV_TOKEN,
    ):
        response = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai-dev",
                "x-dev-authorization": DEV_TOKEN,
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
    assert response.status_code == 200
    assert response.json() == SUCCESSFUL_CHAT_RESPONSE


def test_x_dev_authorization_missing_fxa(mocked_client_integration):
    """Test that x-dev-authorization without Authorization (FxA) returns 422 (validation)."""
    with patch(
        "mlpa.core.auth.authorize.env.MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN",
        DEV_TOKEN,
    ):
        response = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "service-type": "ai-dev",
                "x-dev-authorization": DEV_TOKEN,
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
    assert response.status_code == 422


def test_x_dev_authorization_invalid_token(mocked_client_integration):
    """Test that wrong x-dev-authorization token returns 401."""
    with patch(
        "mlpa.core.auth.authorize.env.MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN",
        DEV_TOKEN,
    ):
        response = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai-dev",
                "x-dev-authorization": "wrong-token",
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
    assert response.status_code == 401


def test_x_dev_authorization_token_not_configured(mocked_client_integration):
    """When token not configured, x-dev-authorization without auth returns 422 (validation)."""
    with patch(
        "mlpa.core.auth.authorize.env.MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN",
        "",
    ):
        response = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "service-type": "ai-dev",
                "x-dev-authorization": "some-token",
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
    assert response.status_code == 422


def test_x_dev_authorization_ignored_for_non_dev_service_type(
    mocked_client_integration,
):
    """Test that x-dev-authorization is ignored when service-type does not end with -dev."""
    with patch(
        "mlpa.core.auth.authorize.env.MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN",
        DEV_TOKEN,
    ):
        # service-type ai (not ai-dev): x-dev-authorization is ignored, regular FxA auth works
        response = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
                "x-dev-authorization": DEV_TOKEN,
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
    assert response.status_code == 200
    assert response.json() == SUCCESSFUL_CHAT_RESPONSE


def test_x_dev_authorization_token_not_configured_with_fxa(mocked_client_integration):
    """When token not configured, x-dev-authorization + FxA returns 401 (invalid x-dev)."""
    with patch(
        "mlpa.core.auth.authorize.env.MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN",
        "",
    ):
        response = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai-dev",
                "x-dev-authorization": "some-token",
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
    assert response.status_code == 401
    assert "Invalid x-dev-authorization" in str(response.json().get("detail", ""))
