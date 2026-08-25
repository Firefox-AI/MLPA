import time
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from mlpa.core.auth.authorize import authorize_filter_request
from mlpa.core.classes import AuthorizedFilterRequest
from mlpa.core.config import (
    ERROR_RESPONSES,
    PRIVACY_FILTER_FILTER_URL,
    PRIVACY_FILTER_MASTER_AUTH_HEADERS,
    env,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.prometheus_metrics import (
    PrometheusResult,
)
from mlpa.core.sanitization import sanitize_response_body
from mlpa.core.utils import raise_and_log

router = APIRouter()

FILTER_SUCCESS_RESPONSE: dict[int | str, dict[str, Any]] = {
    200: {
        "results": [
            {
                "masked_text": "string",
                "spans": [
                    {
                        "category": "string",
                        "text": "string",
                        "start": 0,
                        "end": 0,
                        "score": 0,
                    }
                ],
            }
        ],
        "model_id": "string",
        "num_items": 0,
    }
}


@router.post(
    "/filter",
    tags=["Privacy Filter"],
    description="Filter sensitive information from user data",
    # response_model=PrivacyFilterResponse,
    responses={**FILTER_SUCCESS_RESPONSE, **ERROR_RESPONSES},
)
async def filter(
    request: Request,
    authorized_filter_request: Annotated[
        AuthorizedFilterRequest, Depends(authorize_filter_request)
    ],
):
    user_id = authorized_filter_request.user
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "User not found from authorization response."},
        )

    start_time = time.perf_counter()
    try:
        result = PrometheusResult.ERROR

        client = get_http_client()
        response = await client.post(
            PRIVACY_FILTER_FILTER_URL,
            headers=PRIVACY_FILTER_MASTER_AUTH_HEADERS,
            json=authorized_filter_request.model_dump(
                exclude={"user"}, exclude_none=True
            ),
        )
        try:
            response.raise_for_status()
            data = sanitize_response_body(response.json())
        except httpx.HTTPStatusError as e:
            raise_and_log(e, False, e.response.status_code, "Error filtering data")
        return {
            "results": data.get("results", []),
            "model_id": data.get("model_id"),
            "num_items": data.get("num_items", 0),
        }
    except Exception as e:
        raise_and_log(e, False, 502, "Failed to proxy request to Privacy Filter")
    finally:
        pass
