import asyncio
import importlib.metadata
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from mlpa.core.auth.authorize import authorize_filter_request
from mlpa.core.classes import AuthorizedFilterRequest
from mlpa.core.config import (
    ERROR_RESPONSES,
    LITELLM_INFO_URL,
    LITELLM_MASTER_AUTH_HEADERS,
    LITELLM_READINESS_URL,
    PRIVACY_FILTER_FILTER_URL,
    PRIVACY_FILTER_MASTER_AUTH_HEADERS,
    env,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.pg_services.services import app_attest_pg, litellm_pg
from mlpa.core.prometheus_metrics import (
    AvailabilityReason,
    PrometheusRejectionReason,
    PrometheusResult,
)

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
    "/",
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
            timeout=env.READINESS_CHECK_TIMEOUT_S,
        )
        return {
            "results": response.json().get("results", []),
            "model_id": response.json().get("model_id"),
            "num_items": response.json().get("num_items", 0),
        }
    except Exception:
        result = PrometheusResult.ERROR
        raise
    finally:
        pass
