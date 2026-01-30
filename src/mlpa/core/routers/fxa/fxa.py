import asyncio
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
FXA_DEFAULT_SCOPE = "profile:uid"
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
        tasks = [
            asyncio.create_task(
                run_in_threadpool(client.verify_token, token, scope=scope)
            )
            for scope in FXA_SCOPES
        ]
        try:
            for task in asyncio.as_completed(tasks):
                try:
                    profile = await task
                    result = PrometheusResult.SUCCESS
                    return profile
                except Exception as e:
                    errors.append(e)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.error(f"FxA auth error: {errors}")
        raise HTTPException(status_code=401, detail="Invalid FxA auth")
    finally:
        metrics.validate_fxa_latency.labels(result=result).observe(
            time.perf_counter() - start_time
        )
