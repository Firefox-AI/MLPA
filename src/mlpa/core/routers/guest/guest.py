import time
import uuid

import jwt
from fastapi import APIRouter

from mlpa.core.classes import GuestTokenResponse
from mlpa.core.config import env

router = APIRouter()

GUEST_TOKEN_ISSUER = "mlpa-guest"


def issue_guest_token() -> str:
    now = int(time.time())
    payload = {
        "iss": GUEST_TOKEN_ISSUER,
        "aud": "mlpa",
        "client_id": "guest",
        "scope": "profile:uid",
        "sub": uuid.uuid4().hex,
        "iat": now,
        "exp": now + env.MLPA_GUEST_TOKEN_TTL_SECONDS,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(
        payload,
        env.MLPA_ACCESS_TOKEN_SECRET,
        algorithm="HS256",
        headers={"typ": "at+JWT"},
    )


@router.get("/guest", tags=["Guest"], response_model=GuestTokenResponse)
async def get_guest_token():
    return {
        "access_token": issue_guest_token(),
        "token_type": "Bearer",
        "expires_in": env.MLPA_GUEST_TOKEN_TTL_SECONDS,
    }
