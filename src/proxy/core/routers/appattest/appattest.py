import cbor2
import json
import time
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
import binascii
import os
import hashlib
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from pyattest.configs.apple import AppleConfig
from pyattest.attestation import Attestation
from pyattest.assertion import Assertion
from cryptography.x509.base import load_pem_x509_certificate
from pathlib import Path
from ...config import env
from ...pg_services.services import app_attest_pg
from ...prometheus_metrics import PrometheusResult, metrics

challenge_store = {}

ROOT_CA_PEM = "Apple_App_Attestation_Root_CA.pem"
root_ca = load_pem_x509_certificate(Path(ROOT_CA_PEM).read_bytes())
root_ca_pem = root_ca.public_bytes(serialization.Encoding.PEM)


async def generate_client_challenge(key_id: str) -> str:
	"""Create a unique challenge tied to a key ID"""
	# First check if challenge already exists for key_id (relevant security measure, & they're on PRIMARY KEY key_id)
	stored_challenge = await app_attest_pg.get_challenge(key_id)
	if (
		not stored_challenge
		or time.time() - stored_challenge.get("created_at").timestamp()
		> env.CHALLENGE_EXPIRY_SECONDS
	):
		challenge = binascii.hexlify(os.urandom(32)).decode(
			"utf-8"
		)  # Slightly faster than secrets.token_urlsafe(32)
		await app_attest_pg.store_challenge(key_id, challenge)
		return challenge
	else:
		return stored_challenge["challenge"]


async def validate_challenge(challenge: str, key_id: str) -> bool:
	"""Check that the challenge exists, is fresh, and matches key_id"""
	start_time = time.time()
	stored_challenge = await app_attest_pg.get_challenge(key_id)
	await app_attest_pg.delete_challenge(key_id)  # Remove challenge after one use
	try:
		if (
			not stored_challenge
			or time.time() - stored_challenge.get("created_at").timestamp()
			> env.CHALLENGE_EXPIRY_SECONDS
		):
			return False
		return challenge == stored_challenge["challenge"]
	finally:
		metrics.validate_challenge_latency.observe(time.time() - start_time)


async def verify_attest(key_id: str, challenge: str, attestation_obj: str):
	start_time = time.time()
	config = AppleConfig(
		key_id=key_id,
		app_id=f"{env.APP_DEVELOPMENT_TEAM}.{env.APP_BUNDLE_ID}",
		root_ca=root_ca_pem,
		production=False,
	)

	result = PrometheusResult.ERROR
	try:
		attestation = Attestation(attestation_obj, challenge, config)
		await run_in_threadpool(attestation.verify)

		# Retrieve verified public key
		verified_data = attestation.data["data"]
		credential_id = verified_data["credential_id"]
		auth_data = verified_data["raw"]["authData"]
		cred_id_len = len(credential_id)
		# Offset = 37 bytes (for rpIdHash, flags, counter) + 16 (aaguid) + 2 (len) + cred_id_len
		public_key_offset = 37 + 16 + 2 + cred_id_len
		# Slice the authData to get the raw COSE public key
		cose_public_key_bytes = auth_data[public_key_offset:]
		# Decode the COSE key and convert it to PEM format.
		cose_key_obj = cbor2.loads(cose_public_key_bytes)
		# COSE Key Map for EC2 keys: 1=kty, -1=crv, -2=x, -3=y
		if cose_key_obj.get(1) != 2 or cose_key_obj.get(-1) != 1:  # kty=EC2, crv=P-256
			raise ValueError("Public key is not a P-256 elliptic curve key.")
		x_coord = cose_key_obj.get(-2)
		y_coord = cose_key_obj.get(-3)

		public_key = ec.EllipticCurvePublicNumbers(
			x=int.from_bytes(x_coord, "big"),
			y=int.from_bytes(y_coord, "big"),
			curve=ec.SECP256R1(),
		).public_key()

		public_key_pem = public_key.public_bytes(
			encoding=serialization.Encoding.PEM,
			format=serialization.PublicFormat.SubjectPublicKeyInfo,
		).decode("utf-8")
		result = PrometheusResult.SUCCESS

	except Exception as e:
		raise HTTPException(
			status_code=403, detail=f"Attestation verification failed: {e}"
		)
	finally:
		metrics.validate_app_attest_latency.labels(result=result).observe(
			time.time() - start_time
		)

	# save public_key
	await app_attest_pg.store_key(key_id, public_key_pem)

	return {"status": "success"}


async def verify_assert(key_id: str, assertion: str, payload: dict):
	start_time = time.time()
	payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
	expected_hash = hashlib.sha256(payload_bytes).digest()

	key_info = await app_attest_pg.get_key(key_id)
	if not key_info:
		raise HTTPException(status_code=403, detail="public key not found for key_id")

	public_key_pem = key_info["public_key_pem"].encode()
	public_key_obj = serialization.load_pem_public_key(public_key_pem)

	config = AppleConfig(
		key_id=key_id,
		app_id=f"{env.APP_DEVELOPMENT_TEAM}.{env.APP_BUNDLE_ID}",
		root_ca=root_ca_pem,
		production=False,
	)

	result = PrometheusResult.ERROR
	try:
		assertion_to_test = Assertion(assertion, expected_hash, public_key_obj, config)
		assertion_to_test.verify()
		result = PrometheusResult.SUCCESS
	except Exception as e:
		raise HTTPException(
			status_code=403, detail=f"Assertion verification failed: {e}"
		)
	finally:
		metrics.validate_app_assert_latency.labels(result=result).observe(
			time.time() - start_time
		)

	return {"status": "success"}
