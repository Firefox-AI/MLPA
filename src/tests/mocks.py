from datetime import datetime
from unittest.mock import MagicMock

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.x509 import load_der_x509_certificate
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from loguru import logger
from pyattest.assertion import Assertion
from pyattest.testutils.factories.attestation import apple as apple_factory

from mlpa.core.classes import AuthorizedChatRequest, ChatRequest
from mlpa.core.config import env
from mlpa.core.routers.appattest.appattest import validate_challenge
from mlpa.core.utils import b64decode_safe, parse_app_attest_jwt
from tests.consts import (
    MOCK_FXA_USER_DATA,
    MOCK_JWKS_RESPONSE,
    SUCCESSFUL_CHAT_RESPONSE,
    TEST_FXA_TOKEN,
    TEST_USER_ID,
)


async def mock_verify_attest(
    app_attest_pg, key_id_b64: str, challenge: str, attestation_obj: str
):
    try:
        attestation_data = cbor2.loads(attestation_obj)
        certificate_chain = attestation_data.get("attStmt", {}).get("x5c", [])
        if not certificate_chain:
            raise ValueError("Attestation missing certificate chain data.")
        leaf_certificate = load_der_x509_certificate(certificate_chain[0])
        public_key = leaf_certificate.public_key()
        public_key_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

    except Exception as e:
        logger.error(f"Attestation verification failed: {e}")
        raise HTTPException(status_code=403, detail="Attestation verification failed")
    await app_attest_pg.store_key(key_id_b64, public_key_pem, 0)
    return {"status": "success"}


async def mock_verify_assert(key_id_b64, assertion_obj, payload: ChatRequest):
    # TODO: implement
    return {"status": "success"}


async def mock_app_attest_auth(app_attest_jwt: str):
    app_attest_request = parse_app_attest_jwt(app_attest_jwt, "attest")
    if app_attest_request.key_id_b64 == "invalid_key":
        raise HTTPException(status_code=401, detail="Invalid key")
    challenge = b64decode_safe(
        app_attest_request.challenge_b64, "challenge_b64"
    ).decode()
    if not await validate_challenge(challenge, app_attest_request.key_id_b64):
        raise HTTPException(status_code=401, detail="Invalid or expired challenge")
    return {"username": "testuser"}


async def mock_get_or_create_user(mock_litellm_pg, user_id: str):
    user = await mock_litellm_pg.get_user(user_id)
    if not user:
        await mock_litellm_pg.store_user(user_id, {"data": "testdata"})
        user = await mock_litellm_pg.get_user(user_id)
        return user, True
    return [{"user_id": user_id, "data": "testdata"}, False]


async def mock_get_completion(authorized_chat_request: AuthorizedChatRequest):
    return SUCCESSFUL_CHAT_RESPONSE


class MockAppAttestPGService:
    def __init__(self):
        self.pg = "MOCK NOT IMPLEMENTED"
        self.db_name = "test"
        self.db_url = "test_app_attest"
        self.connected = True
        self.challenges = {}
        self.keys = {}

    async def connect(self):
        pass

    async def disconnect(self):
        pass

    def check_status(self):
        return self.connected

    async def store_challenge(self, key_id_b64: str, challenge: str):
        self.challenges[key_id_b64] = {
            "created_at": datetime.now(),
            "challenge": challenge,
        }

    async def get_challenge(self, key_id_b64: str) -> dict | None:
        return self.challenges.get(key_id_b64)

    async def delete_challenge(self, key_id_b64: str):
        try:
            del self.challenges[key_id_b64]
        except:
            pass

    async def store_key(self, key_id_b64: str, public_key: str, counter: int):
        self.keys[key_id_b64] = {"public_key_pem": public_key, "counter": counter}

    async def get_key(self, key_id_b64: str) -> dict | None:
        return self.keys.get(key_id_b64)

    async def update_key_counter(self, key_id_b64: str, counter: int):
        if key_id_b64 in self.keys:
            self.keys[key_id_b64]["counter"] = counter

    async def delete_key(self, key_id_b64: str):
        del self.keys[key_id_b64]


class MockLiteLLMPGService:
    def __init__(self):
        self.db_name = "test"
        self.db_url = "test_litellm"
        self.connected = True
        self.users = {}

    async def connect(self):
        logger.debug(
            "mock connect called",
        )
        pass

    async def disconnect(self):
        pass

    def check_status(self):
        return self.connected

    async def get_user(self, user_id: str):
        logger.debug(
            f"mock get_user called with user_id: {user_id}",
        )
        return self.users.get(user_id)

    async def store_user(self, user_id: str, data: dict):
        logger.debug(
            f"mock store_user called with user_id: {user_id}, data: {data}",
        )
        self.users[user_id] = data


class MockFxAService:
    def __init__(self, client_id: str, client_secret: str, fxa_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.fxa_url = fxa_url

    def verify_token(self, token: str, scope: str = "profile"):
        if token == TEST_FXA_TOKEN:
            return {"user": TEST_USER_ID}
        return {"error": "Invalid token"}


class MockFxAClientForMockRouter:
    """Mock FxA client specifically for the mock router's JWT verification."""

    def __init__(self, client_id: str, client_secret: str, fxa_url: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.fxa_url = fxa_url
        self.apiclient = MagicMock()

        self.apiclient.get.return_value = MOCK_JWKS_RESPONSE

        self._verify_jwt_token = MagicMock()
        self._verify_jwt_token.return_value = MOCK_FXA_USER_DATA

    def set_jwt_verification_result(self, result):
        """Set the result that _verify_jwt_token should return."""
        self._verify_jwt_token.return_value = result

    def set_jwt_verification_exception(self, exception):
        """Set an exception that _verify_jwt_token should raise."""
        self._verify_jwt_token.side_effect = exception

    def set_jwks_response(self, response):
        """Set the JWKS response that apiclient.get should return."""
        self.apiclient.get.return_value = response

    def set_api_exception(self, exception):
        """Set an exception that apiclient.get should raise."""
        self.apiclient.get.side_effect = exception
