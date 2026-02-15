import base64

import jwt

from tests.consts import TEST_KEY_ID_B64


def get_challenge_b64(client) -> str:
    resp = client.get("/verify/challenge", params={"key_id_b64": TEST_KEY_ID_B64})
    challenge = resp.json()["challenge"]
    return base64.b64encode(challenge.encode()).decode()


def make_jwt(
    jwt_secret: str, challenge_b64: str, bundle_id: str = None, **claims
) -> str:
    payload = {"challenge_b64": challenge_b64, "key_id_b64": TEST_KEY_ID_B64, **claims}
    if bundle_id is not None:
        payload["bundle_id"] = bundle_id
    return jwt.encode(payload, key=jwt_secret, algorithm="HS256")


def auth_headers(token: str, **extra_headers) -> dict:
    return {
        "authorization": f"Bearer {token}",
        "use-app-attest": "true",
        "service-type": "ai",
        **extra_headers,
    }


def patch_apple_config_capture_app_id(mocker) -> dict:
    """Patch AppleConfig to capture the app_id it receives, then raise to stop execution."""
    captured = {}

    def fake_apple_config(*args, **kwargs):
        captured["app_id"] = kwargs.get("app_id")
        raise ValueError("stop here")

    mocker.patch(
        "mlpa.core.routers.appattest.appattest.AppleConfig",
        side_effect=fake_apple_config,
    )
    return captured
