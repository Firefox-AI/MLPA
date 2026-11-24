import json
import time
import uuid
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jwt.utils import base64url_encode

from mlpa.core.config import (
    ERROR_CODE_GUEST_DISABLED,
    ERROR_CODE_GUEST_TOKEN_TOO_OLD,
    ERROR_CODE_MAX_GUESTS_REACHED,
    GUEST_CAPACITY_ID,
    env,
)
from mlpa.core.logger import logger
from mlpa.core.prometheus_metrics import metrics
from mlpa.core.services.services import app_attest_pg

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


def issue_guest_token(sub: str | None = None) -> str:
    """New guest id by default; refresh passes the old sub."""
    now = int(time.time())
    payload = {
        "iss": GUEST_TOKEN_ISSUER,
        "aud": "mlpa",
        "client_id": "guest",
        "scope": "profile:uid",
        "sub": sub or uuid.uuid4().hex,
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


def _decode_guest_token(authorization: str, verify_exp: bool = True) -> dict:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        return jwt.decode(
            token,
            env.MLPA_GUEST_TOKEN_SIGNING_SECRET,
            algorithms=["HS256"],
            audience="mlpa",
            issuer=GUEST_TOKEN_ISSUER,
            options={"require": ["exp", "iat", "sub"], "verify_exp": verify_exp},
        )
    except jwt.PyJWTError as e:
        logger.error(f"Guest token decode error: {e}")
        raise HTTPException(status_code=401, detail="Invalid guest token")


def verify_guest_token(authorization: str) -> str:
    """Guest id (`sub`) from a guest token issued by issue_guest_token."""
    if not env.MLPA_GUEST_ENABLED:
        raise HTTPException(
            status_code=401, detail={"error": ERROR_CODE_GUEST_DISABLED}
        )
    return _decode_guest_token(authorization)["sub"]


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
        404: {
            "description": "Guest tokens are disabled (`MLPA_GUEST_ENABLED=false`).",
            "content": {
                "application/json": {
                    "example": {"detail": {"error": ERROR_CODE_GUEST_DISABLED}}
                }
            },
        },
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
    close the tab.

    The token is valid for `MLPA_GUEST_TOKEN_TTL_SECONDS` (default 60 min,
    set per env in infra), see the JWT `exp` claim. After that, refresh it
    with `POST /verify/v1/guest/refresh` instead of fetching a new one, so
    the guest keeps its id and budget.
    """
    if not env.MLPA_GUEST_ENABLED:
        raise HTTPException(
            status_code=404, detail={"error": ERROR_CODE_GUEST_DISABLED}
        )
    params = {"access_token": issue_guest_token(), "token_type": "Bearer"}
    if "application/json" in accept:
        metrics.guest_tokens_issued_total.labels(flow="json").inc()
        return JSONResponse(params, headers=NO_STORE_HEADERS)
    metrics.guest_tokens_issued_total.labels(flow="redirect").inc()
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


@router.post(
    "/v1/guest/refresh",
    tags=["Guest"],
    responses={
        200: {
            "description": "New token for the same guest id.",
            "content": {
                "application/json": {
                    "example": {"access_token": "eyJ...", "token_type": "Bearer"}
                }
            },
        },
        401: {
            "description": 'Can\'t refresh. On `{"error": 15}` sign in with FxA, otherwise get a new token from `GET /verify/v1/guest` (new guest id).',
            "content": {
                "application/json": {
                    "examples": {
                        "invalid": {
                            "summary": "Not a guest token, or bad signature",
                            "value": {"detail": "Invalid guest token"},
                        },
                        "too_old": {
                            "summary": "iat older than MLPA_GUEST_REFRESH_IDLE_SECONDS",
                            "value": {
                                "detail": {"error": ERROR_CODE_GUEST_TOKEN_TOO_OLD}
                            },
                        },
                        "guest_disabled": {
                            "summary": "Guest kill switch off (MLPA_GUEST_ENABLED=false), sign in with FxA",
                            "value": {"detail": {"error": ERROR_CODE_GUEST_DISABLED}},
                        },
                    }
                }
            },
        },
        403: {
            "description": "The guest's cap slot was cleaned up and guest capacity is full (error code 12). Sign in with FxA.",
            "content": {
                "application/json": {
                    "example": {"detail": {"error": ERROR_CODE_MAX_GUESTS_REACHED}}
                }
            },
        },
    },
)
async def refresh_guest_token(authorization: str = Header("")):
    """
    Refresh a guest token, keeping the guest id (`sub`), so its budget and
    guest cap slot stay the same.

    Send the current token as `Authorization: Bearer ...`, expired or not. It
    refreshes while its `iat` is younger than `MLPA_GUEST_REFRESH_IDLE_SECONDS`
    (default 30 days, set per env in infra). The new token gets a fresh `iat`
    and `exp` (`MLPA_GUEST_TOKEN_TTL_SECONDS`, default 60 min), so each
    refresh restarts the idle clock.

    Call it when chat answers 401 for a guest token, then retry the request
    once with the new token. It's a plain JSON call with no Fastly challenge,
    so there is no browser tab flow. On 401 `{"error": 16}` or
    `"Invalid guest token"`, fall back to `GET /verify/v1/guest`. On
    `{"error": 15}` guests are off, sign in with FxA instead. All errors are
    listed in the Guest section above.
    """
    if not env.MLPA_GUEST_ENABLED:
        raise HTTPException(
            status_code=401, detail={"error": ERROR_CODE_GUEST_DISABLED}
        )
    if not is_guest_token(authorization):
        raise HTTPException(status_code=401, detail="Invalid guest token")
    payload = _decode_guest_token(authorization, verify_exp=False)
    if time.time() - payload["iat"] > env.MLPA_GUEST_REFRESH_IDLE_SECONDS:
        raise HTTPException(
            status_code=401, detail={"error": ERROR_CODE_GUEST_TOKEN_TOO_OLD}
        )

    sub = payload["sub"]
    admitted = True
    if not await app_attest_pg.touch_guest_claim(sub):
        # Infra cleanup frees claims idle for a token TTL, but refresh can
        # bring the guest back later. Claim again so the cap counts it.
        admitted, _ = await app_attest_pg.admit_managed_base_identity(
            sub, GUEST_CAPACITY_ID
        )
    if not admitted:
        raise HTTPException(
            status_code=403, detail={"error": ERROR_CODE_MAX_GUESTS_REACHED}
        )

    metrics.guest_tokens_issued_total.labels(flow="refresh").inc()
    return JSONResponse(
        {"access_token": issue_guest_token(sub=sub), "token_type": "Bearer"},
        headers=NO_STORE_HEADERS,
    )
