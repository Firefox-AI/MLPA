import time

import httpx
from fastapi import HTTPException

from mlpa.core.classes import AuthorizedSearchRequest
from mlpa.core.config import (
    LITELLM_SEARCH_URL,
    LITELLM_VIRTUAL_AUTH_HEADERS,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.logger import logger
from mlpa.core.metrics import record_search_latency
from mlpa.core.prometheus_metrics import PrometheusResult
from mlpa.core.utils import raise_and_log


async def get_search(authorized_search_request: AuthorizedSearchRequest):
    start_time = time.perf_counter()
    body = authorized_search_request.model_dump(exclude_none=True)
    result = PrometheusResult.ERROR
    logger.debug(
        f"Starting a search request using for user {authorized_search_request.user}",
    )
    try:
        client = get_http_client()
        response = await client.post(
            f"{LITELLM_SEARCH_URL}/exa-search",
            headers=LITELLM_VIRTUAL_AUTH_HEADERS,
            json=body,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise_and_log(e)

        data = response.json()

        result = PrometheusResult.SUCCESS
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise_and_log(e, False, 502, "Failed to proxy request")
    finally:
        record_search_latency(result, time.perf_counter() - start_time)
