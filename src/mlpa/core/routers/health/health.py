import asyncio
import importlib.metadata

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from mlpa.core.config import (
    LITELLM_INFO_URL,
    LITELLM_MASTER_AUTH_HEADERS,
    LITELLM_READINESS_URL,
    PRIVACY_FILTER_READINESS_URL,
    env,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.pg_services.services import app_attest_pg, litellm_pg

mlpa_version = importlib.metadata.version("mlpa")
litellm_version = "N/A"
router = APIRouter()

# LiteLLM has used both strings for a healthy top-level status across versions.
_HEALTHY_LITELLM_STATUSES = {"healthy", "connected"}


async def get_litellm_version(client):
    global litellm_version

    if litellm_version != "N/A":
        return litellm_version

    try:
        response = await client.get(
            LITELLM_INFO_URL, timeout=env.READINESS_CHECK_TIMEOUT_S
        )
        litellm_info = response.json()
    except Exception:
        return litellm_version

    litellm_version = (
        litellm_info.get("litellm_version") or litellm_info.get("version") or "N/A"
    )
    return litellm_version


@router.get("/liveness", tags=["Health"])
async def liveness_probe():
    return {"status": "alive"}


async def _fetch_litellm_readiness(client):
    return await client.get(
        LITELLM_READINESS_URL,
        headers=LITELLM_MASTER_AUTH_HEADERS,
        timeout=env.READINESS_CHECK_TIMEOUT_S,
    )


async def _fetch_privacy_filter_readiness(client):
    return await client.get(
        PRIVACY_FILTER_READINESS_URL, timeout=env.READINESS_CHECK_TIMEOUT_S
    )


def _eval_litellm(litellm_http, version) -> tuple[bool, dict]:
    """Map the LiteLLM readiness result to (ready, sub-body).

    Only ready on a 200 with db connected and a healthy top-level status. A
    LiteLLM that is up but not ready can't serve MLPA traffic.
    """
    unreachable = {"version": version, "status": "unreachable"}
    if isinstance(litellm_http, Exception) or litellm_http.status_code != 200:
        return False, unreachable
    try:
        body = litellm_http.json()
    except Exception:
        return False, unreachable

    ready = (
        body.get("db") == "connected"
        and body.get("status") in _HEALTHY_LITELLM_STATUSES
    )
    return ready, {"version": version, **body}


def _eval_privacy_filter(privacy_filter_http) -> tuple[bool, dict]:
    """Map the Privacy Filter readiness result to (ready, sub-body).

    Only ready on a 200 with db connected and a healthy top-level status. A
    Privacy Filter that is up but not ready can't serve MLPA traffic.
    """
    unreachable = {"version": "N/A", "status": "unreachable"}
    if (
        isinstance(privacy_filter_http, Exception)
        or privacy_filter_http.status_code != 200
    ):
        return False, unreachable
    try:
        body = privacy_filter_http.json()
    except Exception:
        return False, unreachable

    ready = body.get("ready") is True
    return ready, {
        "version": body.get("version"),
        "model_id": body.get("model_id"),
    }


@router.get("/readiness", tags=["Health"])
async def readiness_probe():
    client = get_http_client()

    # Run the checks concurrently. return_exceptions keeps one failure from
    # cancelling the rest, and folding the version fetch in here avoids a
    # separate serial round-trip.
    (
        litellm_ok,
        app_attest_ok,
        litellm_http,
        litellm_version,
        privacy_filter_http,
    ) = await asyncio.gather(
        litellm_pg.ping(),
        app_attest_pg.ping(),
        _fetch_litellm_readiness(client),
        get_litellm_version(client),
        _fetch_privacy_filter_readiness(client),
        return_exceptions=True,
    )

    # ping() never raises, but gather could still hand back an exception.
    postgres_connected = litellm_ok is True
    app_attest_connected = app_attest_ok is True

    # get_litellm_version() handles its own errors, but gather could still return one.
    if isinstance(litellm_version, Exception):
        litellm_version = "N/A"
    litellm_ready, litellm_body = _eval_litellm(litellm_http, litellm_version)
    privacy_filter_ready, privacy_filter_body = _eval_privacy_filter(
        privacy_filter_http
    )

    ready = (
        postgres_connected
        and app_attest_connected
        and litellm_ready
        and privacy_filter_ready
    )

    body = {
        "status": "connected" if ready else "degraded",
        "mlpa_version": mlpa_version,
        "pg_server_dbs": {
            "postgres": "connected" if postgres_connected else "offline",
            "app_attest": "connected" if app_attest_connected else "offline",
        },
        "litellm": litellm_body,
        "privacy_filter": privacy_filter_body,
    }

    if ready:
        return body
    return JSONResponse(status_code=503, content=body)
