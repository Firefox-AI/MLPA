import json
import time
import uuid
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jwt.utils import base64url_encode

from mlpa.core.config import env
from mlpa.core.logger import logger

router = APIRouter()

GUEST_TOKEN_ISSUER = "https://mlpa-prod-prod-mozilla.freetls.fastly.net"
# Bump the kid when rotating MLPA_GUEST_TOKEN_SIGNING_SECRET
GUEST_TOKEN_HEADERS = {"typ": "at+JWT", "kid": "mlpa-guest-v1"}
# First segment of every guest token, encoded the way PyJWT does it
GUEST_TOKEN_PREFIX = (
    base64url_encode(
        json.dumps(
            {"alg": "HS256", **GUEST_TOKEN_HEADERS},
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
    ).decode()
    + "."
)


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
        headers=GUEST_TOKEN_HEADERS,
    )


def is_guest_token(authorization: str) -> bool:
    """Routes to guest auth without decoding, verify_guest_token does the real check."""
    return authorization.removeprefix("Bearer ").lstrip().startswith(GUEST_TOKEN_PREFIX)


def verify_guest_token(authorization: str) -> str:
    """Guest id (`sub`) from a guest token issued by issue_guest_token."""
    if not env.MLPA_GUEST_ENABLED:
        raise HTTPException(status_code=401, detail="Guest access is disabled")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = jwt.decode(
            token,
            env.MLPA_GUEST_TOKEN_SIGNING_SECRET,
            algorithms=["HS256"],
            audience="mlpa",
            issuer=GUEST_TOKEN_ISSUER,
            options={"require": ["exp", "iat", "sub"]},
        )
    except jwt.PyJWTError as e:
        logger.error(f"Guest token decode error: {e}")
        raise HTTPException(status_code=401, detail="Invalid guest token")
    return payload["sub"]


# Clients first fetch /verify/v1/guest with `Accept: application/json` and get
# the token in the body. If Fastly answers with a challenge instead, they open
# the same URL in a tab and watch for /verify/v1/guest/success. In the tab flow
# the token goes in the fragment so it never leaves the browser.
NO_STORE_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get(
    "/v1/guest",
    tags=["Guest"],
    responses={
        200: {
            "description": "Token as JSON, when the request sends `Accept: application/json`.",
            "content": {
                "application/json": {
                    "example": {"access_token": "eyJ...", "token_type": "Bearer"}
                }
            },
        },
        302: {
            "description": "Redirect to `/verify/v1/guest/success#access_token=...&token_type=Bearer[&state=...]`",
            "headers": {"Location": {"schema": {"type": "string"}}},
        },
        404: {"description": "Guest tokens are disabled (`MLPA_GUEST_ENABLED=false`)."},
    },
)
async def get_guest_token(
    state: str | None = Query(
        None,
        min_length=1,
        max_length=256,
        description="Optional opaque client value, echoed back in the redirect fragment.",
    ),
    accept: str = Header(""),
):
    """
    Issue a guest access token.

    Fetch with `Accept: application/json` to get the token in the body. If
    Fastly answers with a challenge instead, open this URL in a browser tab
    and pass it. MLPA then redirects to `/verify/v1/guest/success` with the
    token in the URL fragment. Watch the tab URL for that path, read the fragment, then
    close the tab. Token expiry is in the JWT `exp` claim.
    """
    if not env.MLPA_GUEST_ENABLED:
        raise HTTPException(status_code=404, detail="Guest tokens are disabled")
    params = {"access_token": issue_guest_token(), "token_type": "Bearer"}
    if "application/json" in accept:
        return JSONResponse(params, headers=NO_STORE_HEADERS)
    if state is not None:
        params["state"] = state
    fragment = urlencode(params)
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
