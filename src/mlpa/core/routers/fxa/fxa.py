import time
from typing import Annotated

from fastapi import APIRouter, Header

from mlpa.core.prometheus_metrics import PrometheusResult, metrics
from mlpa.core.utils import get_fxa_client

router = APIRouter()
client = get_fxa_client()


def fxa_auth(x_fxa_authorization: Annotated[str | None, Header()]):
    start_time = time.time()
    token = x_fxa_authorization.removeprefix("Bearer ").split()[0]
    result = PrometheusResult.ERROR
    try:
        profile = client.verify_token(token, scope="profile")
        result = PrometheusResult.SUCCESS
    except Exception as e:
        return {"error": f"Invalid FxA auth: {e}"}
    finally:
        metrics.validate_fxa_latency.labels(result=result).observe(
            time.time() - start_time
        )
    return profile
