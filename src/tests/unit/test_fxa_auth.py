import asyncio

import pytest
from fastapi import HTTPException

from mlpa.core.prometheus_metrics import PrometheusResult
from mlpa.core.routers.fxa import fxa as fxa_module


async def test_fxa_auth_returns_first_successful_scope(mocker):
    scopes = ("profile:uid", "scope-a", "scope-b")
    mocker.patch.object(fxa_module, "FXA_SCOPES", scopes)

    async def fake_run_in_threadpool(_fn, _token, *, scope):
        if scope == "scope-b":
            await asyncio.sleep(0.01)
            return {"user": "ok"}
        await asyncio.sleep(0.02)
        raise Exception(f"invalid-{scope}")

    mocker.patch.object(fxa_module, "run_in_threadpool", new=fake_run_in_threadpool)
    mock_metrics = mocker.patch.object(fxa_module, "metrics")

    profile = await fxa_module.fxa_auth("Bearer test-token")

    assert profile == {"user": "ok"}
    mock_metrics.validate_fxa_latency.labels.assert_called_once_with(
        result=PrometheusResult.SUCCESS
    )
    mock_metrics.validate_fxa_latency.labels().observe.assert_called_once()


async def test_fxa_auth_raises_when_all_scopes_fail(mocker):
    scopes = ("profile:uid", "scope-a")
    mocker.patch.object(fxa_module, "FXA_SCOPES", scopes)

    async def fake_run_in_threadpool(_fn, _token, *, scope):
        await asyncio.sleep(0.01)
        raise Exception(f"invalid-{scope}")

    mocker.patch.object(fxa_module, "run_in_threadpool", new=fake_run_in_threadpool)
    mock_metrics = mocker.patch.object(fxa_module, "metrics")

    with pytest.raises(HTTPException) as exc_info:
        await fxa_module.fxa_auth("Bearer test-token")

    assert exc_info.value.status_code == 401
    mock_metrics.validate_fxa_latency.labels.assert_called_once_with(
        result=PrometheusResult.ERROR
    )
    mock_metrics.validate_fxa_latency.labels().observe.assert_called_once()
