import importlib.metadata

from fastapi import APIRouter

from mlpa.core.config import (
    LITELLM_INFO_URL,
    LITELLM_MASTER_AUTH_HEADERS,
    LITELLM_READINESS_URL,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.pg_services.services import app_attest_pg, litellm_pg

mlpa_version = importlib.metadata.version("mlpa")
router = APIRouter()


@router.get("/liveness", tags=["Health"])
async def liveness_probe():
    return {"status": "alive"}


@router.get("/readiness", tags=["Health"])
async def readiness_probe():
    # todo add check to PG and LiteLLM status here
    pg_status = litellm_pg.check_status()
    app_attest_pg_status = app_attest_pg.check_status()
    client = get_http_client()
    response = await client.get(
        LITELLM_READINESS_URL, headers=LITELLM_MASTER_AUTH_HEADERS, timeout=3
    )
    litellm_status = response.json()

    response = await client.get(LITELLM_INFO_URL, timeout=3)
    litellm_info = response.json()
    return {
        "status": "connected",
        "mlpa_version": mlpa_version,
        "pg_server_dbs": {
            "postgres": "connected" if pg_status else "offline",
            "app_attest": "connected" if app_attest_pg_status else "offline",
        },
        "litellm": {
            "litellm_version": litellm_info.get("litellm_version", "N/A"),
            **litellm_status,
        },
    }
