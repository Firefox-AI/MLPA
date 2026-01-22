#!/usr/bin/env python3
"""
Typer CLI for exercising the App Attest QA flow.
Before running run mlpa with APP_ATTEST_QA=true and generate QA certificates.
"""

import asyncio
import base64
import datetime
import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Optional

import cbor2
import httpx
import jwt
import typer
from asn1crypto.core import OctetString
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from cryptography.x509 import load_pem_x509_certificate
from cryptography.x509.extensions import UnrecognizedExtension
from cryptography.x509.oid import NameOID
from pyattest.testutils.factories.certificates import key_usage

from mlpa.core.config import LITELLM_MASTER_AUTH_HEADERS, env
from mlpa.core.pg_services.services import app_attest_pg

app = typer.Typer(
    help="Utilities for registering App Attest devices and requesting completions."
)

QA_CERT_DIR = Path(env.APP_ATTEST_QA_CERT_DIR)
DEFAULT_KEY_ID_PATH = QA_CERT_DIR / "key_id.json"
DEFAULT_BASE_URL = f"http://0.0.0.0:{env.PORT or 8080}"


def generate_attestation_object(
    challenge: str,
    app_id: str,
    key_id_bytes: bytes,
    device_private_key: ec.EllipticCurvePrivateKey,
) -> bytes:
    """
    Generate a CBOR-encoded Apple App Attest attestation object.

    Args:
        challenge: Hex-encoded challenge string from the server
        app_id: App Attest identifier (team_id.bundle_id)
        key_id_bytes: Raw bytes of the key ID
        device_private_key: EC private key for the device

    Returns:
        CBOR-encoded attestation object bytes
    """
    root_key = load_pem_private_key((QA_CERT_DIR / "root_key.pem").read_bytes(), b"123")
    root_cert = load_pem_x509_certificate((QA_CERT_DIR / "root_cert.pem").read_bytes())
    device_public_key = device_private_key.public_key()
    pubkey_uncompressed = device_public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    rp_id_hash = hashlib.sha256(app_id.encode()).digest()
    auth_data = (
        rp_id_hash
        + b"\x00"
        + struct.pack("!I", 0)
        + b"appattestdevelop"
        + struct.pack("!H", len(key_id_bytes))
        + key_id_bytes
    )
    auth_data += cbor2.dumps(
        {
            1: 2,
            3: -7,
            -1: 1,
            -2: pubkey_uncompressed[1:33],
            -3: pubkey_uncompressed[33:],
        }
    )

    challenge_bytes = challenge.encode()
    nonce_hash = hashlib.sha256(
        auth_data + hashlib.sha256(challenge_bytes).digest()
    ).digest()
    der_nonce = bytes(6) + OctetString(nonce_hash).native

    cert = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [x509.NameAttribute(NameOID.ORGANIZATION_NAME, "pyattest-testing-leaf")]
            )
        )
        .issuer_name(root_cert.subject)
        .public_key(device_public_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.now(datetime.UTC))
        .not_valid_after(
            datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=10)
        )
        .add_extension(key_usage, critical=False)
        .add_extension(
            UnrecognizedExtension(
                x509.ObjectIdentifier("1.2.840.113635.100.8.2"), der_nonce
            ),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )

    return cbor2.dumps(
        {
            "fmt": "apple-appattest",
            "attStmt": {
                "x5c": [
                    cert.public_bytes(serialization.Encoding.DER),
                    root_cert.public_bytes(serialization.Encoding.DER),
                ],
                "receipt": b"",
            },
            "authData": auth_data,
        }
    )


async def generate_assertion_object(
    app_id: str,
    key_id_bytes: bytes,
    device_private_key: ec.EllipticCurvePrivateKey,
    payload_hash: bytes,
) -> bytes:
    """
    Generate a CBOR-encoded Apple App Attest assertion object.

    Args:
        app_id: App Attest identifier (team_id.bundle_id)
        key_id_bytes: Raw bytes of the key ID
        device_private_key: EC private key for the device
        payload_hash: SHA256 hash of the request payload

    Returns:
        CBOR-encoded assertion object bytes containing authenticatorData and signature
    """
    # Get current counter from app attest DB for incrementing
    await app_attest_pg.connect()
    key_id_b64 = base64.urlsafe_b64encode(key_id_bytes).decode("utf-8")
    row = await app_attest_pg.get_key(key_id_b64)
    counter = row["counter"] if row else 0
    await app_attest_pg.disconnect()

    auth_data = (
        hashlib.sha256(app_id.encode()).digest()
        + b"\x01"
        + struct.pack("!I", counter + 1)
        + struct.pack("!H", len(key_id_bytes))
        + key_id_bytes
    )
    nonce = hashlib.sha256(auth_data + payload_hash).digest()
    der_signature = device_private_key.sign(nonce, ec.ECDSA(hashes.SHA256()))
    return cbor2.dumps({"authenticatorData": auth_data, "signature": der_signature})


def resolve_urls(base_url: Optional[str]) -> dict[str, str]:
    """
    Resolve API endpoint URLs from a base URL.

    Args:
        base_url: Base URL for the MLPA server (optional, uses default if None)

    Returns:
        Dictionary mapping endpoint names to full URLs
    """
    base = (base_url or DEFAULT_BASE_URL).rstrip("/")
    return {
        "challenge": f"{base}/verify/challenge",
        "attest": f"{base}/verify/attest",
        "completion": f"{base}/v1/chat/completions",
    }


def load_key_material(
    key_id_file: Path,
) -> tuple[dict, str, bytes, ec.EllipticCurvePrivateKey]:
    """
    Load key material from the key_id.json file.

    Args:
        key_id_file: Path to the key_id.json file

    Returns:
        Tuple of (key_data dict, key_id_b64, key_id_bytes, device_private_key)
    """
    data = json.loads(key_id_file.read_text())
    key_id_b64 = data["key_id_b64"]
    device_private_key = load_pem_private_key(
        data["device_private_key_pem"].encode(), password=None
    )
    return data, key_id_b64, device_private_key


def fetch_challenge(url: str, key_id_b64: str) -> str:
    """
    Fetch a challenge from the server for attestation or assertion.

    Args:
        url: Challenge endpoint URL
        key_id_b64: Base64-encoded key ID

    Returns:
        Hex-encoded challenge string

    Raises:
        RuntimeError: If the challenge response is missing the challenge value
        httpx.HTTPStatusError: If the HTTP request fails
    """
    response = httpx.get(url, params={"key_id_b64": key_id_b64}, timeout=30.0)
    response.raise_for_status()
    challenge = response.json().get("challenge")
    if not challenge:
        raise RuntimeError("Challenge response did not include a challenge value.")
    return challenge


def create_attestation_jwt(
    key_id_b64: str, challenge: str, attestation_obj_b64: str
) -> str:
    """
    Create a JWT token for attestation containing App Attest data.

    Args:
        key_id_b64: Base64-encoded key ID
        challenge: Hex-encoded challenge string
        attestation_obj_b64: Base64-encoded attestation object

    Returns:
        JWT token string
    """
    challenge_b64 = base64.urlsafe_b64encode(challenge.encode()).decode("utf-8")
    payload = {
        "key_id_b64": key_id_b64,
        "challenge_b64": challenge_b64,
        "attestation_obj_b64": attestation_obj_b64,
    }
    # JWT signature is not verified by the server, so we can use any secret
    return jwt.encode(payload, key="qa-secret", algorithm="HS256")


def submit_attestation(
    url: str, key_id_b64: str, challenge: str, attestation_obj_b64: str
) -> dict:
    """
    Submit an attestation object to the server for verification.

    Args:
        url: Attestation endpoint URL
        key_id_b64: Base64-encoded key ID
        challenge: Hex-encoded challenge string
        attestation_obj_b64: Base64-encoded attestation object

    Returns:
        Server response as a dictionary

    Raises:
        httpx.HTTPStatusError: If the HTTP request fails
    """
    jwt_token = create_attestation_jwt(key_id_b64, challenge, attestation_obj_b64)
    headers = {
        "authorization": f"Bearer {jwt_token}",
        "use-qa-certificates": "true",
    }
    response = httpx.post(url, headers=headers, timeout=30.0)
    response.raise_for_status()
    return response.json()


def create_assertion_jwt(
    key_id_b64: str, challenge: str, assertion_obj_b64: str
) -> str:
    """
    Create a JWT token for assertion containing App Attest data.

    Args:
        key_id_b64: Base64-encoded key ID
        challenge: Hex-encoded challenge string
        assertion_obj_b64: Base64-encoded assertion object

    Returns:
        JWT token string
    """
    challenge_b64 = base64.urlsafe_b64encode(challenge.encode()).decode("utf-8")
    payload = {
        "key_id_b64": key_id_b64,
        "challenge_b64": challenge_b64,
        "assertion_obj_b64": assertion_obj_b64,
    }
    # JWT signature is not verified by the server, so we can use any secret
    return jwt.encode(payload, key="qa-secret", algorithm="HS256")


def submit_completion(
    url: str, key_id_b64: str, challenge: str, assertion_obj_b64: str, payload: dict
) -> dict:
    """
    Submit a chat completion request with an assertion object.

    Args:
        url: Chat completions endpoint URL
        key_id_b64: Base64-encoded key ID
        challenge: Hex-encoded challenge string
        assertion_obj_b64: Base64-encoded assertion object
        payload: Chat completion request payload (messages, model, etc.)

    Returns:
        Server response as a dictionary

    Raises:
        httpx.HTTPStatusError: If the HTTP request fails
    """
    jwt_token = create_assertion_jwt(key_id_b64, challenge, assertion_obj_b64)
    headers = {
        "authorization": f"Bearer {jwt_token}",
        "use-app-attest": "true",
        "use-qa-certificates": "true",
        "service-type": "ai",
    }
    response = httpx.post(url, json=payload, headers=headers, timeout=30.0)
    response.raise_for_status()
    return response.json()


def build_payload() -> dict:
    """
    Build a default chat completion request payload.

    Returns:
        Dictionary containing stream, messages, model, temperature, max_completion_tokens, and top_p
    """
    return {
        "stream": False,
        "messages": [
            {
                "role": "user",
                "content": "Hello, this is a test message from App Attest QA script.",
            }
        ],
        "model": env.MODEL_NAME,
        "temperature": env.TEMPERATURE,
        "max_completion_tokens": env.MAX_COMPLETION_TOKENS,
        "top_p": env.TOP_P,
        "mock_response": "Ok sure",
    }


def compute_payload_hash(payload: dict) -> bytes:
    """
    Compute the SHA256 hash of a JSON payload for assertion signing.

    Args:
        payload: Dictionary to hash

    Returns:
        SHA256 hash digest as bytes
    """
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload_bytes).digest()


def app_attest_id() -> str:
    """
    Get the App Attest identifier (team_id.bundle_id).

    Returns:
        App Attest identifier string
    """
    return f"{env.APP_DEVELOPMENT_TEAM}.{env.APP_BUNDLE_ID}"


@app.command("register")
def register_device(
    key_id_file: Path = typer.Option(
        DEFAULT_KEY_ID_PATH,
        "--key-id-file",
        help=f"Path to {env.APP_ATTEST_QA_CERT_DIR}/key_id.json",
    ),
    mlpa_url: Optional[str] = typer.Option(
        None, "--mlpa-url", help="Override MLPA base URL (defaults to env/localhost)"
    ),
) -> None:
    """
    Perform App Attest registration (steps 1 and 2). Only required once per device/user.
    """
    _, key_id_b64, device_private_key = load_key_material(key_id_file)
    key_id_bytes = base64.urlsafe_b64decode(key_id_b64)
    urls = resolve_urls(mlpa_url)

    typer.echo("Requesting attestation challenge...")
    challenge = fetch_challenge(urls["challenge"], key_id_b64)
    typer.echo(f"Challenge: {challenge}")

    attestation_obj_b64 = base64.urlsafe_b64encode(
        generate_attestation_object(
            challenge, app_attest_id(), key_id_bytes, device_private_key
        )
    ).decode("utf-8")

    typer.echo("Submitting attestation...")
    result = submit_attestation(
        urls["attest"], key_id_b64, challenge, attestation_obj_b64
    )
    typer.echo(json.dumps(result, indent=2))


@app.command("completion")
def request_completion(
    key_id_file: Path = typer.Option(
        DEFAULT_KEY_ID_PATH,
        "--key-id-file",
        help=f"Path to {env.APP_ATTEST_QA_CERT_DIR}/key_id.json",
    ),
    mlpa_url: Optional[str] = typer.Option(
        None, "--mlpa-url", help="Override MLPA base URL (defaults to env/localhost)"
    ),
) -> None:
    """
    Request a chat completion (steps 3 and 4). Requires prior successful registration.
    """
    _, key_id_b64, device_private_key = load_key_material(key_id_file)
    key_id_bytes = base64.urlsafe_b64decode(key_id_b64)
    urls = resolve_urls(mlpa_url)

    typer.echo("Requesting assertion challenge...")
    challenge = fetch_challenge(urls["challenge"], key_id_b64)
    typer.echo(f"Challenge: {challenge}")

    payload = build_payload()
    payload_hash = compute_payload_hash(payload)
    assertion_obj = asyncio.run(
        generate_assertion_object(
            app_attest_id(), key_id_bytes, device_private_key, payload_hash
        )
    )
    assertion_obj_b64 = base64.urlsafe_b64encode(assertion_obj).decode("utf-8")

    typer.echo("Submitting chat completion request...")
    result = submit_completion(
        urls["completion"], key_id_b64, challenge, assertion_obj_b64, payload
    )
    typer.echo(json.dumps(result, indent=2))


if __name__ == "__main__":
    app()
