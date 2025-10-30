from fastapi import APIRouter, HTTPException

from mlpa.core.classes import AssertionRequest, AttestationRequest
from mlpa.core.routers.appattest import (
	generate_client_challenge,
	validate_challenge,
	verify_assert,
	verify_attest,
)
from mlpa.core.utils import b64decode_safe

router = APIRouter()


@router.get("/challenge", tags=["App Attest"])
async def get_challenge(key_id_b64: str):
	if not key_id_b64:
		raise HTTPException(status_code=400, detail="Missing key_id_b64")
	return {"challenge": await generate_client_challenge(key_id_b64)}


# Attest - send key_id_b64, challenge_b64, attestation_obj_b64
@router.post("/attest", tags=["App Attest"])
async def attest(request: AttestationRequest):
	challenge = b64decode_safe(request.challenge_b64, "challenge_b64")
	if not await validate_challenge(challenge, request.key_id_b64):
		raise HTTPException(status_code=400, detail="Invalid or expired challenge")

	attestation_obj = b64decode_safe(request.attestation_obj_b64, "attestation_obj_b64")
	try:
		result = await verify_attest(request.key_id_b64, challenge, attestation_obj)
	except ValueError as e:
		raise HTTPException(status_code=403, detail=str(e))
	return result


# Assert - send key_id_b64, challenge_b64, assertion_obj_b64, payload
async def app_attest_auth(request: AssertionRequest):
	challenge = b64decode_safe(request.challenge_b64, "challenge_b64")
	if not await validate_challenge(challenge, request.key_id_b64):
		return {"error": "Invalid or expired challenge"}

	assertion_obj = b64decode_safe(request.assertion_obj_b64, "assertion_obj_b64")

	try:
		result = await verify_assert(
			request.key_id_b64,
			assertion_obj,
			request.model_dump(
				exclude={"key_id_b64", "challenge_b64", "assertion_obj_b64"}
			),
		)
	except HTTPException:
		return {"error": "Invalid App Attest auth"}
	except Exception as e:
		return {"error": str(e)}
	return result
