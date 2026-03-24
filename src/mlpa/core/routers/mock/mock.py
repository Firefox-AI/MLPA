import asyncio
import json
from typing import Annotated, Optional

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from fxa.errors import TrustError

from mlpa.core.auth.authorize import authorize_request
from mlpa.core.classes import AuthorizedChatRequest, ChatRequest
from mlpa.core.completions import record_chat_request_rejection
from mlpa.core.config import ERROR_CODE_MAX_USERS_REACHED, env
from mlpa.core.prometheus_metrics import PrometheusRejectionReason
from mlpa.core.utils import get_fxa_client, get_or_create_user
from tests.consts import MOCK_CHAT_RESPONSE, MOCK_STREAMING_CHUNKS

router = APIRouter()
fxa_client = get_fxa_client()


def verify_jwt_token_only(authorization: Annotated[str | None, Header()]):
    """
    Verify JWT token using pyfxa's _verify_jwt_token method without making POST calls.
    This is useful for load testing where we want to verify token validity locally.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing FxA authorization header")

    try:
        token = authorization.removeprefix("Bearer ").split()[0]
    except Exception:
        raise HTTPException(
            status_code=401, detail="Invalid authorization header format"
        )

    try:
        # Get JWKS keys from FxA server (this is a GET request, not POST)
        keys = fxa_client.apiclient.get("/jwks").get("keys", [])

        # Try to verify the token using each key
        for key in keys:
            try:
                result = fxa_client._verify_jwt_token(json.dumps(key), token)
                return result
            except jwt.exceptions.InvalidSignatureError:
                # Try next key if signature doesn't match
                continue
            except jwt.exceptions.PyJWTError as e:
                # Other JWT errors (expired, malformed, etc.)
                raise HTTPException(
                    status_code=401, detail=f"JWT verification failed: {str(e)}"
                )

        # If we get here, no key matched the signature
        raise HTTPException(status_code=401, detail="Invalid token signature")

    except TrustError as e:
        raise HTTPException(status_code=401, detail=f"Token trust error: {str(e)}")
    except Exception as e:
        raise HTTPException(
            status_code=401, detail=f"Token verification failed: {str(e)}"
        )


async def mock_stream():
    for chunk_index in range(len(MOCK_STREAMING_CHUNKS)):
        yield MOCK_STREAMING_CHUNKS[chunk_index]
        await asyncio.sleep(env.MOCK_STREAMING_CHUNK_LATENCY_MS / 1000)


@router.post(
    "/chat/completions",
    description="Mock LiteLLM endpoint with simulated latency.",
    tags=["Mock"],
)
async def chat_completion(
    authorized_chat_request: Annotated[
        Optional[AuthorizedChatRequest], Depends(authorize_request)
    ],
):
    user_id = authorized_chat_request.user
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "User not found from authorization response."},
        )

    try:
        user, _ = await get_or_create_user(user_id)
    except HTTPException as exc:
        if (
            exc.status_code == 403
            and isinstance(exc.detail, dict)
            and exc.detail.get("error") == ERROR_CODE_MAX_USERS_REACHED
        ):
            record_chat_request_rejection(
                authorized_chat_request,
                PrometheusRejectionReason.SIGNUP_CAP_EXCEEDED,
            )
        raise
    if user.get("blocked"):
        raise HTTPException(status_code=403, detail={"error": "User is blocked."})

    await asyncio.sleep(env.MOCK_TTFT_MS / 1000)

    if authorized_chat_request.stream:
        return StreamingResponse(mock_stream(), media_type="text/event-stream")

    return MOCK_CHAT_RESPONSE


@router.post(
    "/chat/completions_no_auth",
    description="Mock LiteLLM endpoint with simulated latency and JWT-only token validation (no POST calls).",
    tags=["Mock"],
)
async def chat_completion_no_auth(
    chat_request: ChatRequest,
    fxa_user_data: Annotated[dict, Depends(verify_jwt_token_only)],
):
    user_id = fxa_user_data.get("user")
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "User not found from JWT token."},
        )

    user, _ = await get_or_create_user(user_id)
    if user.get("blocked"):
        raise HTTPException(status_code=403, detail={"error": "User is blocked."})

    await asyncio.sleep(env.MOCK_TTFT_MS / 1000)

    if chat_request.stream:
        return StreamingResponse(mock_stream(), media_type="text/event-stream")

    return MOCK_CHAT_RESPONSE
