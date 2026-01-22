import binascii
import hashlib
import json
import os
import time
from functools import lru_cache
from pathlib import Path

import cbor2
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.base import load_pem_x509_certificate
from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from pyattest.assertion import Assertion
from pyattest.attestation import Attestation
from pyattest.configs.apple import AppleConfig

from mlpa.core.app_attest import QA_CERT_DIR, ensure_qa_certificates
from mlpa.core.config import env
from mlpa.core.logger import logger
from mlpa.core.pg_services.services import app_attest_pg
from mlpa.core.prometheus_metrics import PrometheusResult, metrics
from mlpa.core.utils import b64decode_safe

challenge_store = {}
PROJECT_ROOT = Path(__file__).resolve().parents[5]


@lru_cache(maxsize=1)
def _load_root_ca(use_qa_certificates: bool) -> bytes:
    """Load the root CA certificate based on APP_ATTEST_QA flag"""
    if env.APP_ATTEST_QA and use_qa_certificates:
        ensure_qa_certificates()
        logger.warning(
            "⚠️  APP_ATTEST_QA is set to TRUE - App Attest will use FAKE QA certificates for testing. "
            "DO NOT use in production!"
        )
        qa_cert_path = QA_CERT_DIR / "root_cert.pem"
        logger.debug(f"Looking for QA certificate at: {qa_cert_path}")
        if qa_cert_path.exists():
            root_ca = load_pem_x509_certificate(qa_cert_path.read_bytes())
            return root_ca.public_bytes(serialization.Encoding.PEM)
        else:
            logger.warning(
                f"APP_ATTEST_QA is enabled but {qa_cert_path} not found. "
                "Falling back to production certificate."
            )

    # Default to production certificate
    root_ca_path = PROJECT_ROOT / "Apple_App_Attestation_Root_CA.pem"
    if not root_ca_path.exists():
        raise FileNotFoundError(
            f"Root CA certificate not found at {root_ca_path}. "
            "For QA testing, set APP_ATTEST_QA=true and run the certificate generation script."
        )
    root_ca = load_pem_x509_certificate(root_ca_path.read_bytes())
    return root_ca.public_bytes(serialization.Encoding.PEM)


async def generate_client_challenge(key_id_b64: str) -> str:
    """Create a unique challenge tied to a key ID"""
    # First check if challenge already exists for key_id (relevant security measure, & they're on PRIMARY KEY key_id_b64)
    stored_challenge = await app_attest_pg.get_challenge(key_id_b64)
    if (
        not stored_challenge
        or time.time() - stored_challenge.get("created_at").timestamp()
        > env.CHALLENGE_EXPIRY_SECONDS
    ):
        challenge = binascii.hexlify(os.urandom(32)).decode(
            "utf-8"
        )  # Slightly faster than secrets.token_urlsafe(32)
        await app_attest_pg.store_challenge(key_id_b64, challenge)
        return challenge
    else:
        return stored_challenge["challenge"]


async def validate_challenge(challenge: str, key_id_b64: str) -> bool:
    """Check that the challenge exists, is fresh, and matches key_id_b64"""
    start_time = time.perf_counter()
    result = PrometheusResult.ERROR
    stored_challenge = await app_attest_pg.get_challenge(key_id_b64)
    await app_attest_pg.delete_challenge(key_id_b64)
    try:
        if (
            not stored_challenge
            or time.time() - stored_challenge.get("created_at").timestamp()
            > env.CHALLENGE_EXPIRY_SECONDS
        ):
            return False
        is_valid = challenge == stored_challenge["challenge"]
        if is_valid:
            result = PrometheusResult.SUCCESS
        return is_valid
    finally:
        metrics.validate_challenge_latency.labels(result=result).observe(
            time.perf_counter() - start_time
        )


async def verify_attest(
    key_id_b64: str,
    challenge: bytes,
    attestation_obj: bytes,
    use_qa_certificates: bool,
):
    start_time = time.perf_counter()
    key_id = b64decode_safe(key_id_b64, "key_id_b64")
    root_ca_pem = _load_root_ca(use_qa_certificates)
    config = AppleConfig(
        key_id=key_id,
        app_id=f"{env.APP_DEVELOPMENT_TEAM}.{env.APP_BUNDLE_ID}",
        root_ca=root_ca_pem,
        production=env.APP_ATTEST_PRODUCTION,
    )

    result = PrometheusResult.ERROR
    try:
        attestation = Attestation(attestation_obj, challenge, config)
        await run_in_threadpool(attestation.verify)

        # Retrieve verified public key
        verified_data = attestation.data["data"]
        credential_id = verified_data["credential_id"]
        auth_data = verified_data["raw"]["authData"]
        attestation_counter = int.from_bytes(auth_data[33:37], "big")
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
        logger.error(f"Attestation verification failed: {e}")
        raise HTTPException(status_code=403, detail="Attestation verification failed")
    finally:
        metrics.validate_app_attest_latency.labels(result=result).observe(
            time.perf_counter() - start_time
        )

    # save public_key in b64
    await app_attest_pg.store_key(key_id_b64, public_key_pem, attestation_counter)

    return {"status": "success"}


async def verify_assert(
    key_id_b64: str, assertion: bytes, payload: dict, use_qa_certificates: bool
):
    start_time = time.perf_counter()
    key_id = b64decode_safe(key_id_b64, "key_id_b64")
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    expected_hash = hashlib.sha256(payload_bytes).digest()

    key_record = await app_attest_pg.get_key(key_id_b64)
    if not key_record or not key_record.get("public_key_pem"):
        logger.error(
            f"Assertion verification failed: No public key found for key_id_b64: {key_id_b64}"
        )
        raise HTTPException(status_code=403, detail="Assertion verification failed")
    public_key_pem = key_record["public_key_pem"]
    last_counter = key_record.get("counter", 0)

    public_key_obj = serialization.load_pem_public_key(public_key_pem.encode())

    root_ca_pem = _load_root_ca(use_qa_certificates)
    config = AppleConfig(
        key_id=key_id,
        app_id=f"{env.APP_DEVELOPMENT_TEAM}.{env.APP_BUNDLE_ID}",
        root_ca=root_ca_pem,
        production=env.APP_ATTEST_PRODUCTION,
    )

    result = PrometheusResult.ERROR
    try:
        assertion_to_test = Assertion(assertion, expected_hash, public_key_obj, config)
        assertion_to_test.verify()
        try:
            unpacked_assertion = cbor2.loads(assertion)
            auth_data = unpacked_assertion["authenticatorData"]
            current_counter = int.from_bytes(auth_data[33:37], "big")
        except Exception as counter_error:
            logger.error(f"Assertion counter parsing failed: {counter_error}")
            raise HTTPException(
                status_code=500, detail="Server error during App Attest auth"
            )

        if current_counter <= last_counter:
            logger.error(
                f"Assertion counter replay detected for key_id_b64={key_id_b64}: "
                f"incoming={current_counter}, stored={last_counter}"
            )
            raise HTTPException(status_code=403, detail="Assertion verification failed")

        await app_attest_pg.update_key_counter(key_id_b64, current_counter)
        result = PrometheusResult.SUCCESS
    except Exception as e:
        logger.error(f"Assertion verification failed: {e}")
        raise HTTPException(status_code=403, detail=f"Assertion verification failed")
    finally:
        metrics.validate_app_assert_latency.labels(result=result).observe(
            time.perf_counter() - start_time
        )

    return {"status": "success"}
