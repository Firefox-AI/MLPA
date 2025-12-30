import time
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException
from loguru import logger

from mlpa.core.prometheus_metrics import PrometheusResult, metrics
from mlpa.core.utils import get_fxa_client

router = APIRouter()
client = get_fxa_client()


def fxa_auth(authorization: Annotated[str | None, Header()]):
    start_time = time.time()
    token = authorization.removeprefix("Bearer ").split()[0]
    result = PrometheusResult.ERROR
    try:
        profile = client.verify_token(token, scope="profile")
        result = PrometheusResult.SUCCESS
    except Exception as e:
        logger.error(f"FxA auth error: {e}")
        raise HTTPException(status_code=401, detail="Invalid FxA auth")
    finally:
        metrics.auth_response_count_total.labels(method="fxa", result=result).inc()
        metrics.auth_duration_seconds.labels(method="fxa", result=result).observe(
            time.time() - start_time
        )
    return profile
