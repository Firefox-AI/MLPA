import base64
import hashlib
import json
import os
import struct
import subprocess
import time
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import cbor2
import jwt
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pyattest.testutils.factories.attestation import apple as apple_factory

from mlpa.core.config import env
from tests.consts import (
    MOCK_MODEL_NAME,
    TEST_BUNDLE_ID,
    TEST_KEY_ID_B64,
    TEST_USER_ID,
)

pytestmark = pytest.mark.smoke

APP_ATTEST_QA_CERT_FILENAMES = (
    "key_id.json",
    "root_cert.pem",
    "root_key.pem",
)
DEFAULT_APP_ATTEST_QA_BUCKET = f"gs://{env.APP_ATTEST_QA_BUCKET}/{(env.APP_ATTEST_QA_BUCKET_PREFIX or '').strip('/')}"


def _chat_payload() -> dict:
    return {
        "model": MOCK_MODEL_NAME,
        "messages": [{"role": "user", "content": "smoke test"}],
        "stream": False,
        "temperature": 0,
        "max_completion_tokens": 8,
        "top_p": 1,
    }


def _json_body(payload: dict) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _post_json(client, path: str, payload: dict, headers: dict):
    return client.post(
        path,
        json=payload,
        headers={"content-type": "application/json", **headers},
    )


def _fxa_headers(
    token: str,
    service_type: str,
    purpose: str | None = "chat",
) -> dict:
    headers = {
        "authorization": f"Bearer {token}",
        "service-type": service_type,
    }
    if purpose is not None:
        headers["purpose"] = purpose
    return headers


def _assert_chat_completion_shape(data: dict) -> None:
    assert isinstance(data, dict)
    assert isinstance(data.get("choices"), list)
    assert data["choices"]

    choice = data["choices"][0]
    assert isinstance(choice, dict)
    assert isinstance(choice.get("message"), dict)

    message = choice["message"]
    assert isinstance(message.get("role"), str)
    assert "content" in message

    usage = data.get("usage")
    if usage is not None:
        assert isinstance(usage, dict)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
            if key in usage:
                assert isinstance(usage[key], int)


def _assert_chat_response(response) -> None:
    assert response.status_code == 200, response.text
    _assert_chat_completion_shape(response.json())


def _challenge(smoke_client, key_id_b64: str) -> str:
    response = smoke_client.get("/verify/challenge", params={"key_id_b64": key_id_b64})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data.get("challenge"), str)
    assert data["challenge"]
    return data["challenge"]


def _challenge_b64(challenge: str) -> str:
    return base64.urlsafe_b64encode(challenge.encode("utf-8")).decode("utf-8")


def _app_attest_jwt(**claims) -> str:
    return jwt.encode(
        {**claims, "iat": int(time.time())},
        key="qa-smoke-secret",
        algorithm="HS256",
    )


def _new_device_material() -> tuple[bytes, str, ec.EllipticCurvePrivateKey]:
    private_key = ec.generate_private_key(ec.SECP256R1())
    public_key = private_key.public_key()
    public_key_uncompressed = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    key_id_bytes = hashlib.sha256(public_key_uncompressed).digest()
    key_id_b64 = base64.urlsafe_b64encode(key_id_bytes).decode("utf-8")
    return key_id_bytes, key_id_b64, private_key


def _qa_cert_dir() -> Path:
    return Path(env.APP_ATTEST_QA_CERT_DIR)


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _load_app_attest_qa_script():
    path = _project_root() / "scripts" / "app_attest_qa" / "app_attest_qa.py"
    spec = spec_from_file_location("app_attest_qa_script", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _qa_cert_file_ready(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def _download_live_app_attest_material() -> None:
    cert_dir = _qa_cert_dir()
    missing = [
        filename
        for filename in APP_ATTEST_QA_CERT_FILENAMES
        if not _qa_cert_file_ready(cert_dir / filename)
    ]
    if not missing:
        return

    cert_dir.mkdir(parents=True, exist_ok=True)
    sources = [
        f"{DEFAULT_APP_ATTEST_QA_BUCKET}/{filename}"
        for filename in APP_ATTEST_QA_CERT_FILENAMES
    ]
    command = ["gcloud", "--quiet", "storage", "cp", *sources, str(cert_dir)]

    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except FileNotFoundError:
        pytest.fail(
            "Live App Attest smoke requires the gcloud CLI to download QA certs. "
            f"Install gcloud or place certs under {cert_dir}."
        )
    except subprocess.CalledProcessError as e:
        pytest.fail(
            "Live App Attest smoke failed to download QA certs with "
            f"`{' '.join(command)}`. stderr: {e.stderr.strip()}"
        )


def _assert_live_app_attest_material() -> None:
    _download_live_app_attest_material()
    cert_dir = _qa_cert_dir()
    missing = [
        path.name
        for path in (
            cert_dir / "root_key.pem",
            cert_dir / "root_cert.pem",
            cert_dir / "key_id.json",
        )
        if not _qa_cert_file_ready(path)
    ]
    if missing:
        pytest.fail(
            "Live App Attest smoke requires matching QA cert material in "
            f"{cert_dir}: {', '.join(missing)}"
        )


def _app_attest_app_id(bundle_id: str) -> str:
    team = os.environ.get("SMOKE_APP_DEVELOPMENT_TEAM") or env.APP_DEVELOPMENT_TEAM
    return f"{team}.{bundle_id}"


def _live_attestation_obj_b64(
    challenge: str,
    app_id: str,
    key_id_bytes: bytes,
    private_key: ec.EllipticCurvePrivateKey,
) -> str:
    _assert_live_app_attest_material()
    app_attest_qa = _load_app_attest_qa_script()

    attestation_obj = app_attest_qa.generate_attestation_object(
        challenge, app_id, key_id_bytes, private_key
    )
    return base64.urlsafe_b64encode(attestation_obj).decode("utf-8")


def _mock_attestation_obj_b64(challenge: str) -> str:
    challenge_bytes = base64.b64encode(challenge.encode("utf-8"))
    attestation_obj, _ = apple_factory.get(app_id="foo", nonce=challenge_bytes)
    return base64.b64encode(attestation_obj).decode("utf-8")


def _assertion_obj_b64(
    app_id: str,
    key_id_bytes: bytes,
    private_key: ec.EllipticCurvePrivateKey,
    payload_hash: bytes,
    counter: int,
) -> str:
    auth_data = (
        hashlib.sha256(app_id.encode("utf-8")).digest()
        + b"\x01"
        + struct.pack("!I", counter)
        + struct.pack("!H", len(key_id_bytes))
        + key_id_bytes
    )
    nonce = hashlib.sha256(auth_data + payload_hash).digest()
    signature = private_key.sign(nonce, ec.ECDSA(hashes.SHA256()))
    assertion_obj = cbor2.dumps(
        {"authenticatorData": auth_data, "signature": signature}
    )
    return base64.urlsafe_b64encode(assertion_obj).decode("utf-8")


def _register_app_attest_key(
    smoke_client, smoke_is_remote: bool
) -> tuple[str, str, str]:
    bundle_id = os.environ.get("SMOKE_APP_BUNDLE_ID", TEST_BUNDLE_ID)
    app_id = _app_attest_app_id(bundle_id)

    if smoke_is_remote:
        key_id_bytes, key_id_b64, private_key = _new_device_material()
    else:
        key_id_bytes = b""
        key_id_b64 = TEST_KEY_ID_B64
        private_key = None

    attest_challenge = _challenge(smoke_client, key_id_b64)
    if smoke_is_remote:
        attestation_obj_b64 = _live_attestation_obj_b64(
            attest_challenge, app_id, key_id_bytes, private_key
        )
    else:
        attestation_obj_b64 = _mock_attestation_obj_b64(attest_challenge)

    token = _app_attest_jwt(
        key_id_b64=key_id_b64,
        challenge_b64=_challenge_b64(attest_challenge),
        attestation_obj_b64=attestation_obj_b64,
        bundle_id=bundle_id,
    )
    response = smoke_client.post(
        "/verify/attest",
        headers={
            "authorization": f"Bearer {token}",
            "use-qa-certificates": "true",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert isinstance(data, dict)
    assert data.get("status") == "success"

    if smoke_is_remote:
        return (
            key_id_b64,
            bundle_id,
            _assertion_obj_b64(
                app_id,
                key_id_bytes,
                private_key,
                hashlib.sha256(_json_body(_chat_payload())).digest(),
                counter=1,
            ),
        )

    assertion_token = _app_attest_jwt(
        key_id_b64=key_id_b64,
        challenge_b64=_challenge_b64(_challenge(smoke_client, key_id_b64)),
        assertion_obj_b64=base64.b64encode(b"smoke-assertion").decode("utf-8"),
        bundle_id=bundle_id,
    )
    return key_id_b64, bundle_id, assertion_token


def test_app_attest_qa_happy_path_shape(smoke_client, smoke_is_remote):
    if smoke_is_remote:
        _assert_live_app_attest_material()

    key_id_b64, bundle_id, assertion = _register_app_attest_key(
        smoke_client, smoke_is_remote
    )

    if smoke_is_remote:
        assertion_token = _app_attest_jwt(
            key_id_b64=key_id_b64,
            challenge_b64=_challenge_b64(_challenge(smoke_client, key_id_b64)),
            assertion_obj_b64=assertion,
            bundle_id=bundle_id,
        )
    else:
        assertion_token = assertion

    response = _post_json(
        smoke_client,
        "/v1/chat/completions",
        _chat_payload(),
        headers={
            "authorization": f"Bearer {assertion_token}",
            "use-app-attest": "true",
            "use-qa-certificates": "true",
            "service-type": "ai",
        },
    )
    _assert_chat_response(response)


def test_play_integrity_happy_path_shape(smoke_client, smoke_is_remote, mocker):
    if smoke_is_remote and not os.environ.get("SMOKE_PLAY_INTEGRITY_TOKEN"):
        # TODO
        pytest.skip(
            "Live Play Integrity smoke requires SMOKE_PLAY_INTEGRITY_TOKEN; "
            "there is no deployed bypass path."
        )

    if smoke_is_remote:
        integrity_token = os.environ["SMOKE_PLAY_INTEGRITY_TOKEN"]
        user_id = os.environ.get("SMOKE_PLAY_INTEGRITY_USER_ID", TEST_USER_ID)
    else:
        integrity_token = "test-token"
        user_id = TEST_USER_ID
        request_hash = hashlib.sha256(user_id.encode("utf-8")).hexdigest()
        mocker.patch(
            "mlpa.core.routers.play.play._decode_integrity_token",
            return_value={
                "tokenPayloadExternal": {
                    "requestDetails": {
                        "requestPackageName": env.PLAY_INTEGRITY_PACKAGE_NAME,
                        "requestHash": request_hash,
                    },
                    "appIntegrity": {"appRecognitionVerdict": "PLAY_RECOGNIZED"},
                    "deviceIntegrity": {
                        "deviceRecognitionVerdict": ["MEETS_DEVICE_INTEGRITY"]
                    },
                }
            },
        )

    verify_response = smoke_client.post(
        "/verify/play",
        json={"integrity_token": integrity_token, "user_id": user_id},
    )
    assert verify_response.status_code == 200
    token_data = verify_response.json()
    assert isinstance(token_data.get("access_token"), str)
    assert token_data.get("token_type") == "Bearer"
    assert isinstance(token_data.get("expires_in"), int)

    response = _post_json(
        smoke_client,
        "/v1/chat/completions",
        _chat_payload(),
        headers={
            "authorization": f"Bearer {token_data['access_token']}",
            "use-play-integrity": "true",
            "service-type": "ai",
            "purpose": "chat",
        },
    )

    _assert_chat_response(response)


def test_fxa_happy_path_shape(smoke_client, smoke_fxa_token):
    response = _post_json(
        smoke_client,
        "/v1/chat/completions",
        _chat_payload(),
        headers=_fxa_headers(smoke_fxa_token, "ai", "chat"),
    )

    _assert_chat_response(response)


def test_smartwindow_happy_path_shape(smoke_client, smoke_fxa_token):
    response = _post_json(
        smoke_client,
        "/v1/chat/completions",
        _chat_payload(),
        headers=_fxa_headers(smoke_fxa_token, "ai", "convo-starters-sidebar"),
    )

    _assert_chat_response(response)


def test_memories_happy_path_shape(smoke_client, smoke_fxa_token):
    response = _post_json(
        smoke_client,
        "/v1/chat/completions",
        _chat_payload(),
        headers=_fxa_headers(smoke_fxa_token, "memories", "memory-generation"),
    )

    _assert_chat_response(response)
