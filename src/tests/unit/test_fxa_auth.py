import pytest
from fastapi import HTTPException

from mlpa.core.auth import fxa as fxa_module
from mlpa.core.prometheus_metrics import PrometheusResult

SCOPES = ("profile:uid", "scope-a", "scope-b")


def patch_verify(mocker, result=None, error=None):
    """Replace run_in_threadpool so verify_token runs inline; return the call log."""
    calls = []

    async def fake_run_in_threadpool(fn, token, **kwargs):
        calls.append((token, kwargs))
        if error:
            raise error
        return result

    mocker.patch.object(fxa_module, "FXA_SCOPES", SCOPES)
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
