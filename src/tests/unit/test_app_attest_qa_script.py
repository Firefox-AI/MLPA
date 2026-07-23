import hashlib
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import cbor2
import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import ec


class _FakeAppAttestPG:
    async def connect(self):
        pass

    async def disconnect(self):
        pass

    async def get_key(self, key_id_b64: str):
        return {"counter": 0}


def _load_app_attest_qa_script():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts"
        / "app_attest_qa"
        / "app_attest_qa.py"
    )
    spec = spec_from_file_location("app_attest_qa_script", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_generate_assertion_object_matches_pyattest_shape():
    app_attest_qa = _load_app_attest_qa_script()
    app_attest_qa.app_attest_pg = _FakeAppAttestPG()

    private_key = ec.generate_private_key(ec.SECP256R1())
    assertion = await app_attest_qa.generate_assertion_object(
        app_id="TEAMID1234.org.mozilla.ios.Fennec",
        key_id_bytes=b"test-key-id",
        device_private_key=private_key,
        payload_hash=hashlib.sha256(b"{}").digest(),
    )

    decoded = cbor2.loads(assertion)
    assert set(decoded) == {"authenticatorData", "signature"}
    assert isinstance(decoded["authenticatorData"], bytes)
    assert isinstance(decoded["signature"], bytes)
    assert int.from_bytes(decoded["authenticatorData"][33:37], "big") == 1


def test_compute_payload_hash_matches_httpx_json_encoding():
    app_attest_qa = _load_app_attest_qa_script()
    payload = app_attest_qa.build_payload()

    request = httpx.Request("POST", "http://example.test", json=payload)

    assert (
        app_attest_qa.compute_payload_hash(payload)
        == hashlib.sha256(request.content).digest()
    )
