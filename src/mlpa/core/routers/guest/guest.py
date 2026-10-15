import time
import uuid
from urllib.parse import urlencode

import jwt
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse

from mlpa.core.config import env

router = APIRouter()

GUEST_TOKEN_ISSUER = "https://mlpa-prod-prod-mozilla.freetls.fastly.net"


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


@router.get(
    "/v1/guest",
    tags=["Guest"],
    status_code=302,
    response_class=RedirectResponse,
    responses={
        302: {
            "description": "Redirect to `/verify/v1/guest/success#access_token=...&token_type=Bearer&state=...`",
            "headers": {"Location": {"schema": {"type": "string"}}},
        },
        404: {"description": "Guest tokens are disabled (`MLPA_GUEST_ENABLED=false`)."},
    },
)
async def get_guest_token(
    state: str = Query(
        min_length=1,
        max_length=256,
        description="Opaque client value, echoed back in the redirect fragment.",
    ),
):
    """
    Issue a guest access token.

    Open this URL in a browser tab, Fastly may show a challenge first. Once
    passed, MLPA redirects to `/verify/v1/guest/success` with the token in the
    URL fragment. Watch the tab URL for that path, read the fragment, then
    close the tab. Token expiry is in the JWT `exp` claim.
    """
    if not env.MLPA_GUEST_ENABLED:
        raise HTTPException(status_code=404, detail="Guest tokens are disabled")
    fragment = urlencode(
        {"access_token": issue_guest_token(), "token_type": "Bearer", "state": state}
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
