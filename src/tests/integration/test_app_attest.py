import base64
import hashlib
import json

import cbor2
import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from pyattest.testutils.factories.attestation import apple as apple_factory

from mlpa.core.config import env
from mlpa.core.routers.appattest import appattest
from tests.consts import (
    SAMPLE_CHAT_REQUEST,
    SUCCESSFUL_CHAT_RESPONSE,
    TEST_BUNDLE_ID,
    TEST_KEY_ID_B64,
)
from tests.integration.appattest_helpers import (
    auth_headers,
    get_challenge_b64,
    make_jwt,
    patch_apple_config_capture_app_id,
)
from tests.mocks import MockAppAttestPGService

sample_chat_request = SAMPLE_CHAT_REQUEST.model_dump(exclude_unset=True)
jwt_secret = "secret"
# TODO: generate keys, certs, attest and assert and pass use-qa-certificates=True for these tests


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
            "bundle_id": TEST_BUNDLE_ID,
        },
        key=jwt_secret,
        algorithm="HS256",
    )
    headers = {
        "Authorization": f"Bearer {app_attest_jwt}",
        "use-app-attest": "true",
        "service-type": "ai",
    }
    response = mocked_client_integration.post(
        "/verify/attest",
        headers=headers,
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
            "bundle_id": TEST_BUNDLE_ID,
            # "attestation_obj_b64" is missing
        },
        key=jwt_secret,
        algorithm="HS256",
    )
    headers = {
        "Authorization": f"Bearer {app_attest_jwt}",
        "use-app-attest": "true",
        "service-type": "ai",
        "use-qa-certificates": "true",
    }
    response = mocked_client_integration.post(
        "/verify/attest",
        headers=headers,
        json=sample_chat_request,
    )
    assert response.status_code == 401


def test_invalid_attestation_request_bad_jwt(mocked_client_integration):
    # Malformed JWT
    bad_jwt = "not.a.valid.jwt"
    headers = {
        "Authorization": f"Bearer {bad_jwt}",
        "use-app-attest": "true",
        "service-type": "ai",
    }
    response = mocked_client_integration.post(
        "/verify/attest",
        headers=headers,
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
            "bundle_id": TEST_BUNDLE_ID,
        },
        key=jwt_secret,
        algorithm="HS256",
    )
    headers = {
        "Authorization": f"Bearer {app_attest_jwt}",
        "use-app-attest": "true",
        "service-type": "ai",
    }
    response = mocked_client_integration.post(
        "/verify/attest",
        headers=headers,
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
            "bundle_id": TEST_BUNDLE_ID,
        },
        key=jwt_secret,
        algorithm="HS256",
    )

    headers = {
        "authorization": f"Bearer {app_attest_jwt}",
        "use-app-attest": "true",
        "service-type": "ai",
    }
    response = mocked_client_integration.post(
        "/verify/attest",
        headers=headers,
        json=sample_chat_request,
    )
    assert response.status_code == 201
    assert response.json() == {"status": "success"}


def test_attest_uses_bundle_id_from_jwt(mocker, mocked_client_integration):
    captured = patch_apple_config_capture_app_id(mocker)

    # Use wraps to bypass the fixture's mock and run the real verify_attest,
    # so execution actually reaches AppleConfig
    mocker.patch(
        "mlpa.core.routers.appattest.middleware.verify_attest",
        wraps=appattest.verify_attest,
    )

    token = make_jwt(
        jwt_secret,
        challenge_b64=get_challenge_b64(mocked_client_integration),
        bundle_id=TEST_BUNDLE_ID,
        attestation_obj_b64=base64.b64encode(b"fake").decode(),
    )

    resp = mocked_client_integration.post(
        "/verify/attest",
        headers=auth_headers(token),
        json=sample_chat_request,
    )

    assert resp.status_code != 200
    assert captured["app_id"] == f"{env.APP_DEVELOPMENT_TEAM}.{TEST_BUNDLE_ID}"


async def test_assert_uses_bundle_id_from_jwt(mocker, mocked_client_integration):
    captured = patch_apple_config_capture_app_id(mocker)

    # Use wraps to bypass the fixture's mock and run the real verify_assert,
    # so execution actually reaches AppleConfig
    mocker.patch(
        "mlpa.core.routers.appattest.middleware.verify_assert",
        wraps=appattest.verify_assert,
    )

    # Mock load_pem_public_key so we don't need a real key
    mocker.patch(
        "mlpa.core.routers.appattest.appattest.serialization.load_pem_public_key",
        return_value="fake",
    )

    # Seed a fake key so verify_assert gets past the public-key lookup
    mock_pg = MockAppAttestPGService()
    await mock_pg.store_key(TEST_KEY_ID_B64, "fake_pem", counter=0)
    mocker.patch("mlpa.core.routers.appattest.appattest.app_attest_pg", mock_pg)

    token = make_jwt(
        jwt_secret,
        challenge_b64=get_challenge_b64(mocked_client_integration),
        bundle_id=TEST_BUNDLE_ID,
        assertion_obj_b64=base64.b64encode(b"fake").decode(),
    )

    resp = mocked_client_integration.post(
        "/v1/chat/completions",
        headers=auth_headers(token),
        json=sample_chat_request,
    )

    assert resp.status_code != 200
    assert captured["app_id"] == f"{env.APP_DEVELOPMENT_TEAM}.{TEST_BUNDLE_ID}"


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
            "bundle_id": TEST_BUNDLE_ID,
        },
        key=jwt_secret,
        algorithm="HS256",
    )

    headers = {
        "authorization": f"Bearer {app_attest_jwt}",
        "use-app-attest": "true",
        "service-type": "ai",
    }
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers=headers,
        json=sample_chat_request,
    )
    assert response.status_code != 401
    assert response.status_code != 400
    assert response.json() == SUCCESSFUL_CHAT_RESPONSE


def _build_fake_assertion(counter: int) -> bytes:
    auth_data = bytearray(37)
    auth_data[33:37] = counter.to_bytes(4, "big")
    return cbor2.dumps({"authenticatorData": bytes(auth_data)})


async def test_verify_assert_rejects_non_monotonic_counter(mocker):
    mock_pg = MockAppAttestPGService()
    mocker.patch("mlpa.core.routers.appattest.appattest.app_attest_pg", mock_pg)

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    await mock_pg.store_key(TEST_KEY_ID_B64, public_key_pem, counter=5)

    mocker.patch("pyattest.assertion.Assertion.verify", return_value=None)

    assertion_bytes = _build_fake_assertion(counter=5)

    with pytest.raises(HTTPException) as exc:
        await appattest.verify_assert(
            TEST_KEY_ID_B64, assertion_bytes, sample_chat_request, False, TEST_BUNDLE_ID
        )

    assert exc.value.status_code == 403


async def test_verify_assert_succeeds_and_updates_counter(mocker):
    mock_pg = MockAppAttestPGService()
    mocker.patch("mlpa.core.routers.appattest.appattest.app_attest_pg", mock_pg)

    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()

    previous_counter = 1
    await mock_pg.store_key(TEST_KEY_ID_B64, public_key_pem, counter=previous_counter)
    mocker.patch("pyattest.assertion.Assertion.verify", return_value=None)

    chat_payload = SAMPLE_CHAT_REQUEST.model_dump(exclude_unset=True)
    payload_bytes = json.dumps(
        chat_payload, sort_keys=True, separators=(",", ":")
    ).encode()
    expected_hash = hashlib.sha256(payload_bytes).digest()

    auth_data = bytearray(37)
    auth_data[32] = 0x01
    next_counter = previous_counter + 1
    auth_data[33:37] = next_counter.to_bytes(4, "big")

    signature = private_key.sign(
        bytes(auth_data) + expected_hash, ec.ECDSA(hashes.SHA256())
    )
    assertion_bytes = cbor2.dumps(
        {
            "authenticatorData": bytes(auth_data),
            "raw": {"signature": signature},
        }
    )

    result = await appattest.verify_assert(
        TEST_KEY_ID_B64, assertion_bytes, chat_payload, False, TEST_BUNDLE_ID
    )
    assert result == {"status": "success"}

    stored_key = await mock_pg.get_key(TEST_KEY_ID_B64)
    assert stored_key["counter"] == next_counter
