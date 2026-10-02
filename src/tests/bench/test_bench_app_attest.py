"""App Attest assertion auth with real signature verification.

Only Postgres is faked. The root CA is the bundled production one: assertion
checks never use it, and the QA certificates would be downloaded from GCS.
"""

import binascii
import hashlib
import os

import pytest

from mlpa.core.classes import AssertionAuth
from mlpa.core.config import env
from mlpa.core.routers.appattest import appattest as appattest_module
from mlpa.core.routers.appattest import middleware as middleware_module
from tests.bench.fakes import APP_ATTEST_BUNDLE_ID, AppAttestDevice, FakeAppAttestPG
from tests.bench.harness import run_async

OPS = 10
REQUEST_HASH = hashlib.sha256(b'{"model":"mock","messages":[]}').digest()


@pytest.fixture(scope="module")
def devices():
    app_id = f"{env.APP_DEVELOPMENT_TEAM}.{APP_ATTEST_BUNDLE_ID}"
    return [AppAttestDevice(f"bench-device-{i}", app_id) for i in range(OPS)]


@pytest.fixture
def pg(monkeypatch):
    pg = FakeAppAttestPG()
    monkeypatch.setattr(appattest_module, "app_attest_pg", pg)
    return pg


def _reset_keys(pg, devices):
    # Assertions carry counter 1, so the stored counter must start below it.
    for device in devices:
        pg.set_key(device.key_id_b64, device.public_key_pem, 0)


def test_verify_assert(benchmark, loop, pg, devices):
    """Load the stored public key, ECDSA-verify, check and bump the counter."""
    assertions = [device.assertion(REQUEST_HASH, counter=1) for device in devices]

    async def verify(i):
        result = await appattest_module.verify_assert(
            devices[i].key_id_b64,
            assertions[i],
            REQUEST_HASH,
            False,
            APP_ATTEST_BUNDLE_ID,
        )
        assert result == {"status": "success"}

    run_async(benchmark, loop, verify, OPS, setup=lambda: _reset_keys(pg, devices))


def test_app_attest_auth(benchmark, loop, pg, devices):
    """Full assertion auth: single-use challenge check plus verify_assert."""
    challenges = [binascii.hexlify(os.urandom(32)).decode() for _ in devices]
    auths = []
    for device, challenge in zip(devices, challenges):
        claims = device.assertion_claims(
            challenge, device.assertion(REQUEST_HASH, counter=1)
        )
        auths.append(AssertionAuth(**claims))

    def setup():
        _reset_keys(pg, devices)
        for device, challenge in zip(devices, challenges):
            pg.set_challenge(device.key_id_b64, challenge)

    async def verify(i):
        result = await middleware_module.app_attest_auth(auths[i], REQUEST_HASH, False)
        assert result == {"status": "success"}

    run_async(benchmark, loop, verify, OPS, setup=setup)
