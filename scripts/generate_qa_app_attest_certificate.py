# scripts/generate_qa_certificates.py
"""
Generate test certificates for local QA using pyattest testutils.
Run this script to create test root CA and certificates for App Attest testing.
"""

import base64
import hashlib
import json
import shutil
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.x509 import load_pem_x509_certificate
from pyattest.testutils.factories.certificates import generate


def generate_key_id_b64_from_certificate(cert_path: Path) -> str:
    """Generate a key_id_b64 from the certificate's public key"""
    cert = load_pem_x509_certificate(cert_path.read_bytes())
    # Get the public key bytes
    public_key_bytes = cert.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # Hash the public key to create a deterministic key_id
    digest = hashlib.sha256(public_key_bytes).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


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

    # Generate key_id_b64 from the certificate
    root_cert_path = certs_dir / "root_cert.pem"
    if root_cert_path.exists():
        key_id_b64 = generate_key_id_b64_from_certificate(root_cert_path)

        # Store key_id info as JSON
        key_id_info = {
            "key_id_b64": key_id_b64,
            "certificate_path": str(root_cert_path),
            "note": "This key_id_b64 is derived from the QA certificate's public key. Use this key_id_b64 for testing App Attest flow with the QA certificates.",
        }

        key_id_json_path = certs_dir / "key_id.json"
        with open(key_id_json_path, "w") as f:
            json.dump(key_id_info, f, indent=2)
        print(f"Created: {key_id_json_path}")
    else:
        print(f"Warning: {root_cert_path} not found, skipping key_id generation")

    print("\n✅ Test certificates generated successfully!")
    print(f"Root CA certificate: {certs_dir / 'root_cert.pem'}")
    print(f"Root CA key: {certs_dir / 'root_key.pem'}")
    print(f"Test key_id: {certs_dir / 'key_id.json'}")
    print(f"\nTest key_id_b64: {key_id_b64}")
    print("\n⚠️  WARNING: These are test certificates only. Do NOT use in production!")


if __name__ == "__main__":
    main()
