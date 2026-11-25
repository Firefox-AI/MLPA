import pytest
from fastapi import HTTPException

from mlpa.core.auth import fxa as fxa_module
from mlpa.core.prometheus_metrics import PrometheusResult


def patch_verify(mocker, result=None, error=None):
    """Replace run_in_threadpool so verify_token runs inline; return the call log."""
    calls = []

    async def fake_run_in_threadpool(fn, token, **kwargs):
        calls.append((token, kwargs))
        if error:
            raise error
        return result

    mocker.patch.object(fxa_module, "FXA_SCOPES", ("profile:uid", "scope-a", "scope-b"))
    mocker.patch.object(fxa_module, "run_in_threadpool", new=fake_run_in_threadpool)
    return calls


async def test_fxa_auth_accepts_token_with_one_of_the_scopes(mocker, metrics_spy):
    profile = {"user": "ok", "scope": ["scope-b"], "verification_source": "local"}
    calls = patch_verify(mocker, result=profile)

    assert await fxa_module.fxa_auth("Bearer test-token") == profile

    # one verify call per request, no per-scope fan-out
    assert calls == [("test-token", {"include_verification_source": True})]
    metrics_spy.assert_only({"validate_fxa_latency", "fxa_verifications_total"})
    assert (
        metrics_spy.histogram_count(
            "validate_fxa_latency",
            result=PrometheusResult.SUCCESS,
            verification_source="local",
        )
        == 1
    )
    assert (
        metrics_spy.value("fxa_verifications_total", verification_source="local") == 1
    )


async def test_fxa_auth_raises_when_token_has_none_of_the_scopes(mocker, metrics_spy):
    patch_verify(
        mocker,
        result={"user": "ok", "scope": ["other"], "verification_source": "local"},
    )

    with pytest.raises(HTTPException) as exc_info:
        await fxa_module.fxa_auth("Bearer test-token")

    assert exc_info.value.status_code == 401
    metrics_spy.assert_only({"validate_fxa_latency"})
    assert (
        metrics_spy.histogram_count(
            "validate_fxa_latency",
            result=PrometheusResult.ERROR,
            verification_source="unknown",
        )
        == 1
    )


async def test_fxa_auth_raises_when_verification_fails(mocker, metrics_spy):
    patch_verify(mocker, error=Exception("invalid token"))

    with pytest.raises(HTTPException) as exc_info:
        await fxa_module.fxa_auth("Bearer test-token")

    assert exc_info.value.status_code == 401
    metrics_spy.assert_only({"validate_fxa_latency"})


def _fxa_client_and_token(exp_offset, sign_with_other_key=False):
    """Real pyfxa client with a local JWKS, and an RS256 FxA-style access token."""
    import json
    import time

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from fxa.oauth import Client

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    signer = (
        rsa.generate_private_key(public_exponent=65537, key_size=2048)
        if sign_with_other_key
        else key
    )
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "uid",
            "client_id": "c",
            "scope": "profile:uid",
            "iat": now - 7200,
            "exp": now + exp_offset,
        },
        signer,
        algorithm="RS256",
        headers={"typ": "at+JWT"},
    )
    return Client("c", "s", "https://fxa.invalid/v1", cache=None, jwks=[jwk]), token


async def test_fxa_auth_expired_token_returns_error_17(mocker):
    from mlpa.core.config import ERROR_CODE_TOKEN_EXPIRED

    client, token = _fxa_client_and_token(exp_offset=-60)
    mocker.patch.object(fxa_module, "client", client)

    with pytest.raises(HTTPException) as exc_info:
        await fxa_module.fxa_auth(f"Bearer {token}")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == {"error": ERROR_CODE_TOKEN_EXPIRED}


async def test_fxa_auth_bad_signature_is_not_error_17(mocker):
    client, token = _fxa_client_and_token(exp_offset=3600, sign_with_other_key=True)
    mocker.patch.object(fxa_module, "client", client)

    with pytest.raises(HTTPException) as exc_info:
        await fxa_module.fxa_auth(f"Bearer {token}")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Invalid FxA auth"
