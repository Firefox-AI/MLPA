import time
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool

from mlpa.core.config import env
from mlpa.core.logger import logger
from mlpa.core.prometheus_metrics import PrometheusResult, metrics
from mlpa.core.utils import get_fxa_client

router = APIRouter()

client = get_fxa_client()
FXA_DEFAULT_SCOPE = "profile"
FXA_SCOPES = tuple(
    scope
    for scope in (
        FXA_DEFAULT_SCOPE,
        env.ADDITIONAL_FXA_SCOPE_1,
        env.ADDITIONAL_FXA_SCOPE_2,
        env.ADDITIONAL_FXA_SCOPE_3,
    )
    if scope
)


async def fxa_auth(authorization: Annotated[str | None, Header()]):
    start_time = time.perf_counter()
    token = authorization.removeprefix("Bearer ").split()[0]
    result = PrometheusResult.ERROR
    errors = []
    try:
        for scope in FXA_SCOPES:
            try:
                profile = await run_in_threadpool(
                    client.verify_token, token, scope=scope
                )
                result = PrometheusResult.SUCCESS
                return profile
            except Exception as e:
                errors.append(e)
        logger.error(f"FxA auth error: {errors}")
        raise HTTPException(status_code=401, detail="Invalid FxA auth")
    finally:
        metrics.validate_fxa_latency.labels(result=result).observe(
            time.perf_counter() - start_time
        )
