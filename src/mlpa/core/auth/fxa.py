import time
from typing import Annotated

from fastapi import Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fxa._utils import scope_matches

from mlpa.core.config import env
from mlpa.core.logger import logger
from mlpa.core.prometheus_metrics import PrometheusResult, metrics
from mlpa.core.utils import get_fxa_client

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
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    token = authorization.removeprefix("Bearer ").split()[0]
    result = PrometheusResult.ERROR
    verification_source = "unknown"
    try:
        try:
            # scope=None: pyfxa verifies and caches the token once, then we
            # check the scopes ourselves (no per-scope fan-out, one thread).
            profile = await run_in_threadpool(
                client.verify_token, token, include_verification_source=True
            )
        except Exception as e:
            logger.error(f"FxA auth error: {e}")
            raise HTTPException(status_code=401, detail="Invalid FxA auth")
        if not any(scope_matches(profile["scope"], scope) for scope in FXA_SCOPES):
            logger.error(
                f"FxA auth error: token scopes {profile['scope']} match none of {FXA_SCOPES}"
            )
            raise HTTPException(status_code=401, detail="Invalid FxA auth")
        result = PrometheusResult.SUCCESS
        verification_source = profile.get("verification_source", "unknown")
        metrics.fxa_verifications_total.labels(
            verification_source=verification_source
        ).inc()
        return profile
    finally:
        metrics.validate_fxa_latency.labels(
            result=result, verification_source=verification_source
        ).observe(time.perf_counter() - start_time)
