from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from mlpa.core.classes import AssertionAuth
from mlpa.core.config import env
from mlpa.core.logger import logger
from mlpa.core.routers.appattest import (
    generate_client_challenge,
    validate_challenge,
    verify_assert,
    verify_attest,
)
from mlpa.core.utils import b64decode_safe, parse_app_attest_jwt

router = APIRouter()


@router.get("/challenge", tags=["App Attest"])
async def get_challenge(key_id_b64: str):
    if not key_id_b64:
        raise HTTPException(status_code=400, detail="Bad Request: missing key_id_b64")
    # iOS key id generation not urlsafe workaround
    key_id_b64 = key_id_b64.replace(" ", "+")
    return {"challenge": await generate_client_challenge(key_id_b64)}


# Attest validation
@router.post("/attest", tags=["App Attest"], status_code=201)
async def attest(
    authorization: Annotated[str | None, Header()] = None,
    use_qa_certificates: Annotated[bool | None, Header()] = None,
):
    attestationAuth = parse_app_attest_jwt(authorization, "attest")
    challenge_bytes = b64decode_safe(attestationAuth.challenge_b64, "challenge_b64")
    if not await validate_challenge(
        challenge_bytes.decode(), attestationAuth.key_id_b64
    ):
        raise HTTPException(status_code=401, detail="Invalid or expired challenge")

    attestation_obj = b64decode_safe(
        attestationAuth.attestation_obj_b64, "attestation_obj_b64"
    )
    try:
        result = await verify_attest(
            attestationAuth.key_id_b64,
            challenge_bytes,
            attestation_obj,
            use_qa_certificates,
            attestationAuth.bundle_id,
        )
    except ValueError as e:
        logger.error(f"App Attest attestation error: {e}")
        raise HTTPException(status_code=401, detail="Invalid App Attest attestation")
    return result


# Assert validation
async def app_attest_auth(
    assertionAuth: AssertionAuth,
    expected_hash: bytes,
    use_qa_certificates: bool,
):
    challenge_bytes = b64decode_safe(assertionAuth.challenge_b64, "challenge_b64")
    if not await validate_challenge(challenge_bytes.decode(), assertionAuth.key_id_b64):
        raise HTTPException(status_code=401, detail="Invalid or expired challenge")

    assertion_obj = b64decode_safe(assertionAuth.assertion_obj_b64, "assertion_obj_b64")

    try:
        result = await verify_assert(
            assertionAuth.key_id_b64,
            assertion_obj,
            expected_hash,
            use_qa_certificates,
            assertionAuth.bundle_id,
        )
    except HTTPException:
        raise HTTPException(status_code=401, detail="Invalid App Attest assertion")
    except Exception as e:
        logger.error(f"App Attest auth error: {e}")
        raise HTTPException(
            status_code=500, detail="Server error during App Attest auth"
        )
    return result
