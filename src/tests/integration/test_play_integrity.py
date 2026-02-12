import hashlib

from mlpa.core.config import env
from mlpa.core.utils import issue_mlpa_access_token
from tests.consts import SAMPLE_CHAT_REQUEST, SUCCESSFUL_CHAT_RESPONSE, TEST_USER_ID


def _mock_decode_payload(request_hash: str) -> dict:
    return {
        "tokenPayloadExternal": {
            "requestDetails": {
                "requestPackageName": env.PLAY_INTEGRITY_PACKAGE_NAME,
                "requestHash": request_hash,
            },
            "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED"},
            "deviceIntegrity": {"deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]},
        }
    }


def test_verify_play_integrity_success(mocked_client_integration, mocker):
    request_hash = hashlib.sha256(TEST_USER_ID.encode("utf-8")).hexdigest()
    mocker.patch(
        "mlpa.core.routers.play.play._decode_integrity_token",
        return_value=_mock_decode_payload(request_hash),
    )

    response = mocked_client_integration.post(
        "/verify/play",
        json={"integrity_token": "test-token", "user_id": TEST_USER_ID},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["access_token"]
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == env.MLPA_ACCESS_TOKEN_TTL_SECONDS


def test_verify_play_integrity_invalid_hash(mocked_client_integration, mocker):
    mocker.patch(
        "mlpa.core.routers.play.play._decode_integrity_token",
        return_value=_mock_decode_payload("bad-hash"),
    )

    response = mocked_client_integration.post(
        "/verify/play",
        json={"integrity_token": "test-token", "user_id": TEST_USER_ID},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid request hash"


def test_verify_play_integrity_missing_payload(mocked_client_integration, mocker):
    mocker.patch(
        "mlpa.core.routers.play.play._decode_integrity_token",
        return_value={},
    )

    response = mocked_client_integration.post(
        "/verify/play",
        json={"integrity_token": "test-token", "user_id": TEST_USER_ID},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Play Integrity token"


def test_chat_with_play_integrity_token_success(mocked_client_integration):
    access_token = issue_mlpa_access_token(TEST_USER_ID)
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {access_token}",
            "use-play-integrity": "true",
            "service-type": "ai",
        },
        json=SAMPLE_CHAT_REQUEST.model_dump(exclude_unset=True),
    )

    assert response.status_code == 200
    assert response.json() == SUCCESSFUL_CHAT_RESPONSE
