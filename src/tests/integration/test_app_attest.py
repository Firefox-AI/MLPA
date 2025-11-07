import base64

import jwt
from pyattest.testutils.factories.attestation import apple as apple_factory

from mlpa.core.config import env
from tests.consts import SAMPLE_CHAT_REQUEST, SUCCESSFUL_CHAT_RESPONSE, TEST_KEY_ID_B64

sample_chat_request = SAMPLE_CHAT_REQUEST.model_dump()
jwt_secret = "secret"


def test_get_challenge(mocked_client_integration):
    response = mocked_client_integration.get(
        "/verify/challenge",
        params={
            "key_id_b64": TEST_KEY_ID_B64,
        },
    )
    assert response.json().get("challenge") is not None
    assert len(response.json().get("challenge")) > 0


def test_invalid_methods(mocked_client_integration):
    response = mocked_client_integration.post(
        "/verify/challenge",
        params={
            "key_id_b64": TEST_KEY_ID_B64,
        },
    )
    assert response.status_code == 405

    response = mocked_client_integration.put(
        "/verify/challenge",
        json={
            "key_id_b64": TEST_KEY_ID_B64,
        },
    )
    assert response.status_code == 405

    response = mocked_client_integration.delete(
        "/verify/challenge",
        params={
            "key_id_b64": TEST_KEY_ID_B64,
        },
    )
    assert response.status_code == 405

    response = mocked_client_integration.get(
        "/verify/attest",
    )
    assert response.status_code == 405

    response = mocked_client_integration.put(
        "/verify/attest",
    )
    assert response.status_code == 405


def test_invalid_challenge(mocked_client_integration):
    challenge = "bad_challenge"
    challenge_b64 = base64.b64encode(challenge.encode()).decode()
    app_attest_jwt = jwt.encode(
        {
            "challenge_b64": challenge_b64,
            "key_id_b64": TEST_KEY_ID_B64,
            "attestation_obj_b64": "VEVTVF9BVFRFU1RBVElPTl9CQVNFNjRVUkw=",
        },
        key=jwt_secret,
        algorithm="HS256",
    )
    response = mocked_client_integration.post(
        "/verify/attest",
        headers={"Authorization": f"Bearer {app_attest_jwt}", "use-app-attest": "true"},
        json=sample_chat_request,
    )
    assert response.json() == {"detail": "Invalid or expired challenge"}


def test_invalid_attestation_request_missing_fields(mocked_client_integration):
    # Missing attestation_obj_b64
    challenge_response = mocked_client_integration.get(
        "/verify/challenge", params={"key_id_b64": TEST_KEY_ID_B64}
    )
    challenge = challenge_response.json().get("challenge")
    challenge_b64 = base64.b64encode(challenge.encode()).decode()
    app_attest_jwt = jwt.encode(
        {
            "challenge_b64": challenge_b64,
            "key_id_b64": TEST_KEY_ID_B64,
            # "attestation_obj_b64" is missing
        },
        key=jwt_secret,
        algorithm="HS256",
    )
    response = mocked_client_integration.post(
        "/verify/attest",
        headers={"Authorization": f"Bearer {app_attest_jwt}", "use-app-attest": "true"},
        json=sample_chat_request,
    )
    assert response.status_code == 401


def test_invalid_attestation_request_bad_jwt(mocked_client_integration):
    # Malformed JWT
    bad_jwt = "not.a.valid.jwt"
    response = mocked_client_integration.post(
        "/verify/attest",
        headers={"Authorization": f"Bearer {bad_jwt}", "use-app-attest": "true"},
        json=sample_chat_request,
    )
    assert response.status_code == 401 or response.json().get("detail") is not None


def test_invalid_attestation_request_wrong_key_id(mocked_client_integration):
    # Use a wrong key_id_b64
    challenge_response = mocked_client_integration.get(
        "/verify/challenge", params={"key_id_b64": TEST_KEY_ID_B64}
    )
    challenge = challenge_response.json().get("challenge")
    challenge_b64 = base64.b64encode(challenge.encode()).decode()
    app_attest_jwt = jwt.encode(
        {
            "challenge_b64": challenge_b64,
            "key_id_b64": "invalid_key_id_b64",
            "attestation_obj_b64": "VEVTVF9BVFRFU1RBVElPTl9CQVNFNjRVUkw=",
        },
        key=jwt_secret,
        algorithm="HS256",
    )
    response = mocked_client_integration.post(
        "/verify/attest",
        headers={"Authorization": f"Bearer {app_attest_jwt}", "use-app-attest": "true"},
        json=sample_chat_request,
    )
    assert response.status_code == 400 or response.json().get("detail") is not None


def test_successful_attestation_request(mocked_client_integration):
    challenge_response = mocked_client_integration.get(
        "/verify/challenge", params={"key_id_b64": TEST_KEY_ID_B64}
    )

    challenge = challenge_response.json().get("challenge")
    challenge_bytes = base64.b64encode(challenge.encode())
    challenge_b64 = challenge_bytes.decode()
    attestation_obj, _ = apple_factory.get(app_id="foo", nonce=challenge_bytes)
    attestation_obj_b64 = base64.b64encode(attestation_obj).decode()
    app_attest_jwt = jwt.encode(
        {
            "challenge_b64": challenge_b64,
            "key_id_b64": TEST_KEY_ID_B64,
            "attestation_obj_b64": attestation_obj_b64,
        },
        key=jwt_secret,
        algorithm="HS256",
    )

    response = mocked_client_integration.post(
        "/verify/attest",
        headers={"authorization": f"Bearer {app_attest_jwt}", "use-app-attest": "true"},
        json=sample_chat_request,
    )
    assert response.status_code == 201
    assert response.json() == {"status": "success"}


def test_successful_request_with_mocked_app_attest_auth(mocked_client_integration):
    challenge_response = mocked_client_integration.get(
        "/verify/challenge", params={"key_id_b64": TEST_KEY_ID_B64}
    )

    challenge = challenge_response.json().get("challenge")
    challenge_b64 = base64.b64encode(challenge.encode()).decode()
    app_attest_jwt = jwt.encode(
        {
            "challenge_b64": challenge_b64,
            "key_id_b64": TEST_KEY_ID_B64,
            "assertion_obj_b64": "VEVTVF9BU1NFUlRJT05fQkFTRTY0VVJM",
        },
        key=jwt_secret,
        algorithm="HS256",
    )

    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={"authorization": f"Bearer {app_attest_jwt}", "use-app-attest": "true"},
        json=sample_chat_request,
    )
    assert response.status_code != 401
    assert response.status_code != 400
    assert response.json() == SUCCESSFUL_CHAT_RESPONSE
