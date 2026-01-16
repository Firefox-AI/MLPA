import hashlib
from functools import lru_cache

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from pydantic import BaseModel

from mlpa.core.classes import PlayIntegrityRequest
from mlpa.core.config import env
from mlpa.core.http_client import get_http_client
from mlpa.core.utils import issue_mlpa_access_token, raise_and_log

router = APIRouter()

PLAY_INTEGRITY_SCOPE = "https://www.googleapis.com/auth/playintegrity"
ALLOWED_DEVICE_VERDICTS = {
    "MEETS_DEVICE_INTEGRITY",
    "MEETS_BASIC_INTEGRITY",
    "MEETS_STRONG_INTEGRITY",
}


@lru_cache(maxsize=1)
def _get_service_account_credentials():
    return service_account.Credentials.from_service_account_file(
        env.PLAY_INTEGRITY_SERVICE_ACCOUNT_FILE,
        scopes=[PLAY_INTEGRITY_SCOPE],
    )


def _get_play_integrity_access_token() -> str:
    credentials = _get_service_account_credentials()
    if not credentials.valid:
        credentials.refresh(Request())
    if not credentials.token:
        raise HTTPException(status_code=500, detail="Failed to fetch access token")
    return credentials.token


async def _decode_integrity_token(integrity_token: str) -> dict:
    access_token = await run_in_threadpool(_get_play_integrity_access_token)
    client = get_http_client()
    try:
        response = await client.post(
            f"https://playintegrity.googleapis.com/v1/{env.PLAY_INTEGRITY_PACKAGE_NAME}:decodeIntegrityToken",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"integrity_token": integrity_token},
            timeout=env.PLAY_INTEGRITY_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise_and_log(e, False, 401)
    except Exception as e:
        raise_and_log(e, False, 502, "Play Integrity validation service unavailable")
    return response.json()


def _validate_integrity_payload(payload: dict, expected_hash) -> None:
    request_details = payload.get("requestDetails", {})
    package_name = request_details.get("requestPackageName")
    if package_name and package_name != env.PLAY_INTEGRITY_PACKAGE_NAME:
        raise HTTPException(status_code=401, detail="Invalid package name")

    token_request_hash = request_details.get("requestHash")
    if token_request_hash != expected_hash:
        raise HTTPException(status_code=401, detail="Invalid request hash")

    app_integrity = payload.get("appIntegrity", {})
    if app_integrity.get("appRecognitionVerdict") != "PLAY_RECOGNIZED":
        raise HTTPException(status_code=401, detail="App not recognized by Play")

    device_integrity = payload.get("deviceIntegrity", {})
    device_verdicts = set(device_integrity.get("deviceRecognitionVerdict", []))
    if not device_verdicts.intersection(ALLOWED_DEVICE_VERDICTS):
        raise HTTPException(status_code=401, detail="Device integrity check failed")


@router.post("/play", tags=["Play Integrity"])
async def verify_play_integrity(payload: PlayIntegrityRequest):
    decoded = await _decode_integrity_token(payload.integrity_token)
    token_payload = decoded.get("tokenPayloadExternal") or decoded.get("tokenPayload")
    if not token_payload:
        raise HTTPException(status_code=401, detail="Invalid Play Integrity token")

    expected_hash = hashlib.sha256(payload.user_id.encode("utf-8")).hexdigest()

    _validate_integrity_payload(token_payload, expected_hash)

    access_token = issue_mlpa_access_token(payload.user_id)
    return {
        "access_token": access_token,
        "token_type": "Bearer",
        "expires_in": env.MLPA_ACCESS_TOKEN_TTL_SECONDS,
    }
