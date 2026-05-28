import asyncio

import pytest
from fastapi import HTTPException

from mlpa.core.prometheus_metrics import PrometheusResult
from mlpa.core.routers.fxa import fxa as fxa_module


async def test_fxa_auth_returns_first_successful_scope(mocker, metrics_spy):
    scopes = ("profile:uid", "scope-a", "scope-b")
    mocker.patch.object(fxa_module, "FXA_SCOPES", scopes)

    async def fake_run_in_threadpool(
        _fn, _token, *, scope, include_verification_source
    ):
        if scope == "scope-b":
            await asyncio.sleep(0.01)
            return {"user": "ok", "verification_source": "local"}
        await asyncio.sleep(0.02)
        raise Exception(f"invalid-{scope}")

    mocker.patch.object(fxa_module, "run_in_threadpool", new=fake_run_in_threadpool)

    profile = await fxa_module.fxa_auth("Bearer test-token")

    assert profile == {"user": "ok", "verification_source": "local"}
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


async def test_fxa_auth_raises_when_all_scopes_fail(mocker, metrics_spy):
    scopes = ("profile:uid", "scope-a")
    mocker.patch.object(fxa_module, "FXA_SCOPES", scopes)

    async def fake_run_in_threadpool(
        _fn, _token, *, scope, include_verification_source
    ):
        await asyncio.sleep(0.01)
        raise Exception(f"invalid-{scope}")

    mocker.patch.object(fxa_module, "run_in_threadpool", new=fake_run_in_threadpool)

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
