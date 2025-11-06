#!/usr/bin/env python3
"""
Generate a test attestation_obj_b64 for QA testing.
This uses pyattest's testutils to create a valid attestation object.
"""

import base64
import datetime
import json
import struct
import sys
from hashlib import sha256
from pathlib import Path

import cbor2
from asn1crypto.core import OctetString
from cryptography import x509
from cryptography.hazmat._oid import ObjectIdentifier
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization.base import load_pem_private_key
from cryptography.x509.base import load_pem_x509_certificate
from cryptography.x509.extensions import UnrecognizedExtension
from cryptography.x509.oid import NameOID
from pyattest.testutils.factories.certificates import key_usage


def generate_attestation_object(
    challenge: str,
    app_id: str,
    key_id_bytes: bytes,
) -> tuple[bytes, bytes]:
    """
    Generate a test attestation object for QA using pyattest's testutils logic.

    Returns:
        tuple: (attestation_object_bytes, public_key_bytes)
    """
    # Load QA certificates directly from qa_certificates directory
    certs_dir = Path("qa_certificates")
    root_cert_path = certs_dir / "root_cert.pem"
    root_key_path = certs_dir / "root_key.pem"

    # Load the root certificate and key
    root_key = load_pem_private_key(root_key_path.read_bytes(), b"123")
    root_cert = load_pem_x509_certificate(root_cert_path.read_bytes())

    # Generate a device key pair (this will be the "device" public key in the attestation)
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER, format=serialization.PublicFormat.PKCS1
    )

    # Create authData
    # authData structure: rpIdHash (32) + flags (1) + signCount (4) + aaguid (16) + credentialIdLength (2) + credentialId + publicKey
    auth_data_public_key = public_key
    auth_data = (
        sha256(app_id.encode()).digest()
        + b"\x00"  # Flag
        + struct.pack("!I", 0)  # Counter
        + b"appattestdevelop"  # AAGUID (16 bytes)
        + struct.pack("!H", 32)  # Credential ID length (SHA-256 digest is 32 bytes)
        + sha256(auth_data_public_key).digest()  # Credential ID (hash of public key)
    )

    # Create nonce: hash of authData + hash of challenge
    nonce = challenge.encode() if isinstance(challenge, str) else challenge
    nonce_hash = sha256(auth_data + sha256(nonce).digest())

    # Create DER-encoded nonce for certificate extension
    # When comparing this nonce with the one calculated on the server, we'll strip 6 bytes which are normally
    # used to indicated an ASN1 envelope sequence. See the verify_nonce method in the apple verifier.
    der_nonce = bytes(6) + OctetString(nonce_hash.digest()).native

    # Create leaf certificate signed by root
    subject = x509.Name(
        [x509.NameAttribute(NameOID.ORGANIZATION_NAME, "pyattest-testing-leaf")]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(root_cert.subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=10))
        .add_extension(key_usage, critical=False)
        .add_extension(
            UnrecognizedExtension(
                ObjectIdentifier("1.2.840.113635.100.8.2"), der_nonce
            ),
            critical=False,
        )
        .sign(root_key, hashes.SHA256())
    )

    # Create attestation object structure
    data = {
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

    # Encode as CBOR using Python cbor2 (not _cbor2 C extension)
    attestation_obj_bytes = cbor2.dumps(data)

    return attestation_obj_bytes, public_key


def main():
    certs_dir = Path("qa_certificates")
    key_id_json_path = certs_dir / "key_id.json"

    if not key_id_json_path.exists():
        print(
            f"Error: {key_id_json_path} not found. Please run generate_qa_app_attest_certificate.py first."
        )
        return

    # Load existing key_id.json
    with open(key_id_json_path, "r") as f:
        key_id_data = json.load(f)

    key_id_b64 = key_id_data["key_id_b64"]

    # Check for QA certificates
    root_cert_path = certs_dir / "root_cert.pem"
    root_key_path = certs_dir / "root_key.pem"

    if not root_cert_path.exists():
        print(f"Error: {root_cert_path} not found.")
        return

    if not root_key_path.exists():
        print(f"Error: {root_key_path} not found.")
        return

    # Get challenge from command line or use placeholder
    if len(sys.argv) > 1:
        challenge = sys.argv[1]
        print(f"Using challenge from command line: {challenge}")
    else:
        challenge = "test_challenge_placeholder"
        print(f"⚠️  No challenge provided. Using placeholder: {challenge}")
        print(
            "   Usage: python scripts/generate_qa_attestation.py <challenge_from_api>"
        )
        print(
            "   Get challenge from: GET /verify/challenge?key_id_b64=<your_key_id_b64>"
        )

    # Get app_id from config or use default
    try:
        from mlpa.core.config import env

        app_id = f"{env.APP_DEVELOPMENT_TEAM}.{env.APP_BUNDLE_ID}"
    except:
        app_id = "TEAMID1234.org.example.app"
        print(f"⚠️  Using default app_id: {app_id}")

    print("Generating attestation object using pyattest testutils...")
    print(f"Using key_id_b64: {key_id_b64}")
    print(f"Using app_id: {app_id}")

    try:
        attestation_obj_bytes, public_key_bytes = generate_attestation_object(
            challenge=challenge,
            app_id=app_id,
            key_id_bytes=base64.urlsafe_b64decode(key_id_b64 + "=="),
        )
        attestation_obj_b64 = (
            base64.urlsafe_b64encode(attestation_obj_bytes).decode().rstrip("=")
        )

        # Base64-encode the challenge for use in API requests
        challenge_b64 = (
            base64.urlsafe_b64encode(challenge.encode()).decode().rstrip("=")
        )

        # Update key_id.json
        key_id_data["attestation_obj_b64"] = attestation_obj_b64
        key_id_data["challenge_used"] = challenge
        key_id_data["challenge_b64"] = challenge_b64
        key_id_data["app_id"] = app_id
        if "note_attestation" not in key_id_data:
            key_id_data["note_attestation"] = (
                "This attestation_obj_b64 was generated for QA testing using pyattest testutils. "
                "Regenerate it with the actual challenge from /verify/challenge endpoint if needed."
            )

        with open(key_id_json_path, "w") as f:
            json.dump(key_id_data, f, indent=2)

        print(f"\n✅ Attestation object generated and added to {key_id_json_path}")
        print(f"   Challenge used: {challenge}")
        print(f"   challenge_b64 (for API): {challenge_b64}")
        print(
            f"\n⚠️  IMPORTANT: The attestation_obj_b64 only works with the challenge it was created with."
        )
        print(
            "   If you get a new challenge from the API, regenerate the attestation with:"
        )
        print(f"   python scripts/generate_qa_attestation.py <new_challenge>")
        print(
            f"\n📝 NOTE: Use the 'challenge_b64' field from key_id.json when making API requests."
        )
        print(
            f"   The challenge from the API is a hex string, but challenge_b64 must be base64-encoded."
        )

    except Exception as e:
        print(f"Error generating attestation object: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    main()
