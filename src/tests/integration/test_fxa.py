from tests.consts import SUCCESSFUL_CHAT_RESPONSE, TEST_FXA_TOKEN


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
