#!/usr/bin/env python3
"""
Generate test certificates for local QA using pyattest testutils.
Run this script to create test root CA and certificates for App Attest testing.
This script also generates a device key pair and derives key_id from the EC public key.
"""

import base64
import hashlib
import json
import shutil
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509 import load_pem_x509_certificate
from pyattest.testutils.factories.certificates import generate


def generate_key_id_from_ec_public_key(
    public_key: ec.EllipticCurvePublicKey,
) -> tuple[bytes, str, str]:
    """Generate key_id from an EC public key using uncompressed point format

    Args:
        public_key: Elliptic curve public key (SECP256R1)

    Returns:
        tuple: (key_id_bytes, key_id_hex, key_id_b64)
    """
    # Get uncompressed point format (0x04 + X + Y, 65 bytes total)
    pubkey_uncompressed = public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    # Hash the uncompressed public key to create key_id
    key_id_bytes = hashlib.sha256(pubkey_uncompressed).digest()
    # Base64 encode the raw bytes (not the hex string)
    key_id_b64 = base64.urlsafe_b64encode(key_id_bytes).decode("utf-8")
    return key_id_bytes, key_id_b64


def main():
    certs_dir = Path("qa_certificates")
    certs_dir.mkdir(exist_ok=True)

    # Create both directory structures that generate() expects
    # Note: pyattest library has inconsistent paths:
    # - root_key.pem is written to "pyattest/testutils/fixtures" (correct)
    # - root_cert.pem is written to "pyatest/testutils/fixtures" (typo)
    correct_fixtures_dir = Path("pyattest/testutils/fixtures")
    typo_fixtures_dir = Path("pyatest/testutils/fixtures")
    correct_fixtures_dir.mkdir(parents=True, exist_ok=True)
    typo_fixtures_dir.mkdir(parents=True, exist_ok=True)

    print("Generating test certificates for QA...")

    # Generate certificates - they will be written to different paths due to library bug
    generate()

    # Copy root_key.pem from correct path
    root_key_src = correct_fixtures_dir / "root_key.pem"
    if root_key_src.exists():
        root_key_dst = certs_dir / "root_key.pem"
        shutil.copy(root_key_src, root_key_dst)
        print(f"Created: {root_key_dst}")
    else:
        print(f"Warning: {root_key_src} not found after generation")

    # Copy root_cert.pem from typo path to correct path, then to qa_certificates
    root_cert_src = typo_fixtures_dir / "root_cert.pem"
    if root_cert_src.exists():
        # Copy to correct path first
        root_cert_correct = correct_fixtures_dir / "root_cert.pem"
        shutil.copy(root_cert_src, root_cert_correct)
        print(f"Copied root_cert.pem to correct path: {root_cert_correct}")

        # Then copy to qa_certificates
        root_cert_dst = certs_dir / "root_cert.pem"
        shutil.copy(root_cert_src, root_cert_dst)
        print(f"Created: {root_cert_dst}")
    else:
        print(f"Warning: {root_cert_src} not found after generation")

    # Clean up: delete both directories
    if typo_fixtures_dir.exists():
        shutil.rmtree(typo_fixtures_dir.parent.parent)  # Remove "pyatest" directory
        print(f"Cleaned up typo directory: {typo_fixtures_dir.parent.parent}")

    if correct_fixtures_dir.exists():
        shutil.rmtree(correct_fixtures_dir.parent.parent)  # Remove "pyattest" directory
        print(f"Cleaned up pyattest directory: {correct_fixtures_dir.parent.parent}")

    # Generate a device EC key pair and derive key_id from it
    # This matches the notebook approach where key_id is derived from the device public key
    print("\nGenerating device EC key pair for key_id...")
    device_private_key = ec.generate_private_key(ec.SECP256R1())
    device_public_key = device_private_key.public_key()

    key_id_bytes, key_id_b64 = generate_key_id_from_ec_public_key(device_public_key)

    # Store the device private key (PEM format) for use in attestation generation
    device_private_key_pem = device_private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    # Store device public key in uncompressed format for reference
    pubkey_uncompressed = device_public_key.public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )

    # Store key_id info as JSON (all formats for convenience)
    key_id_info = {
        "key_id_b64": key_id_b64,  # For API calls (base64 of raw bytes)
        "key_id_bytes": base64.b64encode(key_id_bytes).decode(
            "utf-8"
        ),  # Base64-encoded bytes for storage
        "device_private_key_pem": device_private_key_pem.decode(
            "utf-8"
        ),  # Device private key for attestation generation
        "device_public_key_uncompressed_b64": base64.b64encode(
            pubkey_uncompressed
        ).decode("utf-8"),  # Uncompressed public key
        "note": "key_id_b64 is base64 of raw SHA256(pubkey_uncompressed) bytes. Use key_id_b64 for API calls.",
    }

    key_id_json_path = certs_dir / "key_id.json"
    with open(key_id_json_path, "w") as f:
        json.dump(key_id_info, f, indent=2)
    print(f"Created: {key_id_json_path}")

    print("\n✅ Test certificates generated successfully!")
    print(f"Root CA certificate: {certs_dir / 'root_cert.pem'}")
    print(f"Root CA key: {certs_dir / 'root_key.pem'}")
    print(f"Device key pair and key_id: {certs_dir / 'key_id.json'}")
    print(f"\nTest key_id formats:")
    print(f"  key_id_b64 (for API calls): {key_id_b64}")
    print("\n⚠️  WARNING: These are test certificates only. Do NOT use in production!")


if __name__ == "__main__":
    main()
