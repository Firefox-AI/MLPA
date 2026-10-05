"""Shared fixtures for the auth benchmarks.

Everything the benches need lives in this directory so scripts/bench_compare.py
can copy it onto a checkout of main and run identical bench code on both sides.
"""

import asyncio
import time
from pathlib import Path

import pytest

from mlpa.core.auth import fxa as fxa_module
from mlpa.core.config import env
from tests.bench.fakes import (
    ACCESS_TOKEN_SECRET,
    DEV_AUTH_TOKEN,
    FXA_SCOPE,
    StubFxAClient,
)

BENCH_DIR = Path(__file__).parent
SPIN_UP_SECONDS = 0.2


def pytest_collection_modifyitems(config, items):
    # Keep `make test` fast: benchmarks only run under --benchmark-only.
    if config.getoption("benchmark_only", default=False):
        return
    skip = pytest.mark.skip(reason="benchmark; run with `make bench`")
    for item in items:
        if BENCH_DIR in item.path.parents:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _spin_up_cpu():
    # Busy-wait briefly so a bench that follows a mostly-idle one (e.g. a
    # regression that sleeps) does not start on a downclocked or slower core.
    deadline = time.perf_counter() + SPIN_UP_SECONDS
    while time.perf_counter() < deadline:
        pass


@pytest.fixture(scope="session")
def loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(autouse=True)
def _bench_env(monkeypatch):
    # Pin settings the benches depend on so a local .env cannot skew results.
    monkeypatch.setattr(env, "MLPA_REQUIRE_PURPOSE_HEADER", False)
    monkeypatch.setattr(env, "ALLOW_CUSTOM_VIRTUAL_KEY", False)
    monkeypatch.setattr(env, "APP_ATTEST_PRODUCTION", False)
    monkeypatch.setattr(env, "MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN", DEV_AUTH_TOKEN)
    monkeypatch.setattr(env, "MLPA_ACCESS_TOKEN_SECRET", ACCESS_TOKEN_SECRET)
    monkeypatch.setattr(fxa_module, "FXA_SCOPES", (FXA_SCOPE,))


@pytest.fixture
def stub_fxa_client(monkeypatch):
    monkeypatch.setattr(fxa_module, "client", StubFxAClient())
