"""In-memory stand-ins and fixed credentials for the auth benchmarks.

Kept separate from src/tests/mocks.py on purpose: the bench comparison copies
this directory onto main, and the benches must not depend on test helpers that
may differ between the two sides.
"""

import base64
import hashlib
import json
from datetime import datetime

import cbor2
import jwt
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

# Fixed claims so tokens are identical across runs (exp is year 2100).
ISSUED_AT = 1_700_000_000
EXPIRES_AT = 4_102_444_800

FXA_USER_ID = "bench-fxa-user"
FXA_SCOPE = "profile:uid"
FXA_STUB_TOKEN = "bench-fxa-token"

ACCESS_TOKEN_SECRET = "bench-access-secret"
DEV_AUTH_TOKEN = "bench-dev-token"
PLAY_USER_ID = "bench-play-user"

APP_ATTEST_BUNDLE_ID = "org.mozilla.bench"


class StubFxAClient:
    """Accepts one token, so only MLPA's own FxA handling is measured."""

    def verify_token(self, token, scope=None, include_verification_source=False):
        if token != FXA_STUB_TOKEN:
            raise ValueError("invalid token")
        return {"user": FXA_USER_ID, "verification_source": "local"}


class FakeAppAttestPG:
    """In-memory replacement for the AppAttestPGService calls on the auth path.

    `set_challenge` and `set_key` seed test data; they are not part of the
    real service's interface.
    """

    def __init__(self):
        self.challenges: dict[str, dict] = {}
        self.keys: dict[str, dict] = {}

    def set_challenge(self, key_id_b64: str, challenge: str):
        self.challenges[key_id_b64] = {
            "challenge": challenge,
            "created_at": datetime.now(),
        }

    def set_key(self, key_id_b64: str, public_key_pem: str, counter: int):
        self.keys[key_id_b64] = {"public_key_pem": public_key_pem, "counter": counter}

    async def get_challenge(self, key_id_b64: str):
        return self.challenges.get(key_id_b64)

    async def delete_challenge(self, key_id_b64: str):
        self.challenges.pop(key_id_b64, None)

    async def get_key(self, key_id_b64: str):
        return self.keys.get(key_id_b64)

    async def update_key_counter(self, key_id_b64: str, counter: int):
        self.keys[key_id_b64]["counter"] = counter


class FxASigner:
    """RS256 key pair that signs FxA-style access tokens (typ at+jwt)."""

    def __init__(self):
        self._key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.jwk = json.loads(
            jwt.algorithms.RSAAlgorithm.to_jwk(self._key.public_key())
        ) | {"kid": "bench-key", "alg": "RS256", "use": "sig"}

    def token(self) -> str:
        claims = {
            "sub": FXA_USER_ID,
            "client_id": "bench-client",
            "scope": FXA_SCOPE,
            "iat": ISSUED_AT,
            "exp": EXPIRES_AT,
        }
        return jwt.encode(
            claims,
            self._key,
            algorithm="RS256",
            headers={"typ": "at+jwt", "kid": "bench-key"},
        )


def play_access_token() -> str:
    """An MLPA access token as issued after a Play Integrity check."""
    claims = {
        "sub": PLAY_USER_ID,
        "iat": ISSUED_AT,
        "exp": EXPIRES_AT,
        "iss": "mlpa",
        "typ": "mlpa_access",
    }
    return jwt.encode(claims, ACCESS_TOKEN_SECRET, algorithm="HS256")


class AppAttestDevice:
    """A simulated iOS device with an attested P-256 key.

    Builds assertions in Apple's format (CBOR of signature + authenticatorData)
    so the real pyattest/cryptography verification runs.
    """

    def __init__(self, name: str, app_id: str):
        self._key = ec.generate_private_key(ec.SECP256R1())
        self._rp_id_hash = hashlib.sha256(app_id.encode()).digest()
        self.key_id_b64 = base64.b64encode(
            hashlib.sha256(name.encode()).digest()
        ).decode()
        self.public_key_pem = (
            self._key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

    def assertion(self, client_data_hash: bytes, counter: int) -> bytes:
        auth_data = self._rp_id_hash + b"\x01" + counter.to_bytes(4, "big")
        nonce = hashlib.sha256(auth_data + client_data_hash).digest()
        signature = self._key.sign(nonce, ec.ECDSA(hashes.SHA256()))
        return cbor2.dumps({"signature": signature, "authenticatorData": auth_data})

    def assertion_claims(self, challenge: str, assertion: bytes) -> dict:
        return {
            "key_id_b64": self.key_id_b64,
            "challenge_b64": base64.urlsafe_b64encode(challenge.encode()).decode(),
            "assertion_obj_b64": base64.urlsafe_b64encode(assertion).decode(),
            "bundle_id": APP_ATTEST_BUNDLE_ID,
        }

    def assertion_jwt(self, challenge: str, assertion: bytes) -> str:
        """The bearer token the iOS client sends (MLPA does not verify its signature)."""
        claims = {"iat": ISSUED_AT, **self.assertion_claims(challenge, assertion)}
        return jwt.encode(claims, "unused", algorithm="HS256")
