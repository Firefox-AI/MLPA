import time

import httpx
from fastapi import HTTPException

from mlpa.core.classes import AuthorizedSearchRequest
from mlpa.core.config import (
    LITELLM_SEARCH_URL,
    LITELLM_VIRTUAL_AUTH_HEADERS,
)
from mlpa.core.errors import classify_upstream_error
from mlpa.core.http_client import get_http_client
from mlpa.core.logger import logger
from mlpa.core.metrics import record_search_latency, record_search_request_rejection
from mlpa.core.prometheus_metrics import PrometheusResult
from mlpa.core.sanitization import sanitize_request_body, sanitize_response_body
from mlpa.core.utils import raise_and_log


async def get_search(authorized_search_request: AuthorizedSearchRequest):
    start_time = time.perf_counter()
    body = sanitize_request_body(
        authorized_search_request.model_dump(exclude_none=True)
    )
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
            match = classify_upstream_error(
                error_text=e.response.text,
                status_code=e.response.status_code,
                user=authorized_search_request.user,
            )
            if match is not None:
                if match.log_message:
                    logger.warning(match.log_message)
                record_search_request_rejection(authorized_search_request, match.reason)
                headers = (
                    {"Retry-After": match.retry_after} if match.retry_after else None
                )
                raise HTTPException(
                    status_code=match.http_status,
                    detail={"error": match.error_code},
                    headers=headers,
                )
            raise_and_log(e)

        data = sanitize_response_body(response.json())

        result = PrometheusResult.SUCCESS
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise_and_log(e, False, 502, "Failed to proxy request")
    finally:
        record_search_latency(result, time.perf_counter() - start_time)
