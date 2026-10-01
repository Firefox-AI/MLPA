"""FxA bearer-token auth (`fxa_auth`) and dev-key auth (`auth_with_key`)."""

import fxa.oauth
import pytest

from mlpa.core.auth import fxa as fxa_module
from mlpa.core.auth.dev_auth import auth_with_key
from mlpa.core.auth.fxa import fxa_auth
from tests.bench.fakes import DEV_AUTH_TOKEN, FXA_STUB_TOKEN, FXA_USER_ID, FxASigner
from tests.bench.harness import run_async

OPS = 20


@pytest.fixture(scope="module")
def signer():
    return FxASigner()


def _pyfxa_client(monkeypatch, signer, cache: bool | None):
    # pyfxa disables its cache with None (False is treated as a cache object).
    client = fxa.oauth.Client(
        "bench-client",
        None,
        "https://oauth.invalid/v1",
        cache=cache,
        jwks=[signer.jwk],
    )

    def no_network(*args, **kwargs):
        raise AssertionError("bench must not reach the FxA server")

    # Guards: a silent fallback to the network or to PyJWT would change what
    # is being measured, so fail loudly instead.
    monkeypatch.setattr(client.apiclient, "get", no_network)
    monkeypatch.setattr(client.apiclient, "post", no_network)
    monkeypatch.setattr(fxa.oauth.jwt, "decode", no_network)
    monkeypatch.setattr(fxa_module, "client", client)
    return client


async def _check(profile_coro):
    profile = await profile_coro
    assert profile["user"] == FXA_USER_ID


def test_fxa_auth_stub_client(benchmark, loop, stub_fxa_client):
    """MLPA's own overhead: per-scope tasks, threadpool hop, metrics."""
    header = f"Bearer {FXA_STUB_TOKEN}"
    run_async(benchmark, loop, lambda _: _check(fxa_auth(header)), OPS)


def test_fxa_auth_pyfxa_local_verify(benchmark, loop, monkeypatch, signer):
    """Real pyfxa client, cache miss: RS256 verify against local JWKs."""
    _pyfxa_client(monkeypatch, signer, cache=None)
    header = f"Bearer {signer.token()}"
    run_async(benchmark, loop, lambda _: _check(fxa_auth(header)), OPS)


def test_fxa_auth_pyfxa_cache_hit(benchmark, loop, monkeypatch, signer):
    """Real pyfxa client, token already verified once and cached."""
    _pyfxa_client(monkeypatch, signer, cache=True)
    header = f"Bearer {signer.token()}"
    loop.run_until_complete(_check(fxa_auth(header)))
    run_async(benchmark, loop, lambda _: _check(fxa_auth(header)), OPS)


def test_dev_auth_stub_client(benchmark, loop, stub_fxa_client):
    """x-dev-authorization compare, then FxA (stub client)."""
    header = f"Bearer {FXA_STUB_TOKEN}"
    run_async(
        benchmark,
        loop,
        lambda _: _check(auth_with_key(DEV_AUTH_TOKEN, header)),
        OPS,
    )
