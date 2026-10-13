import time
import uuid
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

from mlpa.core.config import env

router = APIRouter()

GUEST_TOKEN_ISSUER = "https://fxa.mozilla.org"


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
        env.MLPA_GUEST_TOKEN_SIGNING_SECRET,
        algorithm="HS256",
        headers={"typ": "at+JWT"},
    )


# Clients open /verify/v1/guest in a tab so Fastly can show its challenge, then
# watch the URL for /verify/v1/guest/success. The token goes in the fragment so
# it never leaves the browser and stays out of Fastly, LB and MLPA logs.
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get("/v1/guest", tags=["Guest"])
async def get_guest_token():
    fragment = urlencode(
        {
            "access_token": issue_guest_token(),
            "token_type": "Bearer",
            "expires_in": env.MLPA_GUEST_TOKEN_TTL_SECONDS,
        }
    )
    return RedirectResponse(
        f"/verify/v1/guest/success#{fragment}",
        status_code=302,
        headers=NO_STORE_HEADERS,
    )


@router.get("/v1/guest/success", include_in_schema=False)
async def guest_success():
    return HTMLResponse(
        "<!doctype html><title>Done</title><p>You can close this tab.</p>",
        headers=NO_STORE_HEADERS,
    )
