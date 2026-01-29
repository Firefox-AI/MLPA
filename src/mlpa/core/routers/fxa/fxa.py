import time
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from mlpa.core.config import env
from mlpa.core.logger import logger
from mlpa.core.prometheus_metrics import PrometheusResult, metrics
from mlpa.core.utils import get_fxa_client

router = APIRouter()

client = get_fxa_client()
FXA_DEFAULT_SCOPE = "profile"
scopes = filter(
    None,
    [
        FXA_DEFAULT_SCOPE,
        env.ADDITIONAL_FXA_SCOPE_1,
        env.ADDITIONAL_FXA_SCOPE_2,
        env.ADDITIONAL_FXA_SCOPE_3,
    ],
)


async def fxa_auth(authorization: Annotated[str | None, Header()]):
    start_time = time.perf_counter()
    token = authorization.removeprefix("Bearer ").split()[0]
    result = PrometheusResult.ERROR
    success = False
    errors = []
    for scope in scopes:
        try:
            profile = client.verify_token(token, scope=scope)
            result = PrometheusResult.SUCCESS
            success = True
            break
        except Exception as e:
            errors.append(e)
            continue
        finally:
            metrics.validate_fxa_latency.labels(result=result).observe(
                time.time() - start_time
            )
    if not success:
        logger.error(f"FxA auth error: {errors}")
        raise HTTPException(status_code=401, detail="Invalid FxA auth")
    return profile
