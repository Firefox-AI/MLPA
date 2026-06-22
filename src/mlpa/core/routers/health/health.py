import asyncio
import importlib.metadata

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from mlpa.core.config import (
    LITELLM_INFO_URL,
    LITELLM_MASTER_AUTH_HEADERS,
    LITELLM_READINESS_URL,
    env,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.logger import logger
from mlpa.core.migrations import expected_heads
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

    litellm_version = litellm_info.get("litellm_version", "N/A")
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


def _eval_litellm(litellm_http, version) -> tuple[bool, dict]:
    """Map the LiteLLM readiness result to (ready, sub-body).

    Ready only when the HTTP status is 200, db is connected, and the top-level
    status is a known-healthy string. A live-but-not-ready LiteLLM is not enough
    to serve MLPA traffic.
    """
    unreachable = {"litellm_version": version, "status": "unreachable"}
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
    return ready, {"litellm_version": version, **body}


@router.get("/readiness", tags=["Health"])
async def readiness_probe():
    client = get_http_client()

    # Independent checks run concurrently; a failure in one must not cancel the
    # others, so each result (including a raised exception) is reported. The
    # version fetch joins the gather so it never adds a serial round-trip.
    litellm_ok, revisions, litellm_http, version = await asyncio.gather(
        litellm_pg.ping(),
        app_attest_pg.current_revisions(),
        _fetch_litellm_readiness(client),
        get_litellm_version(client),
        return_exceptions=True,
    )

    # litellm pool — ping() never raises, but guard the gather contract anyway.
    postgres_connected = litellm_ok is True

    # app_attest pool liveness is the revision read: a returned set ⇒ connected,
    # a raised connection/timeout ⇒ offline.
    app_attest_connected = isinstance(revisions, (set, frozenset))
    current = set(revisions) if app_attest_connected else set()

    # Expected heads from the files the code ships; resolution failure ⇒ not ready.
    try:
        expected = set(expected_heads())
        heads_resolved = True
    except Exception:
        logger.error(
            "readiness: could not resolve expected Alembic heads", exc_info=True
        )
        expected = set()
        heads_resolved = False

    migration_ok = heads_resolved and app_attest_connected and current == expected

    # get_litellm_version() swallows its own errors, but guard the gather contract.
    if isinstance(version, Exception):
        version = "N/A"
    litellm_ready, litellm_body = _eval_litellm(litellm_http, version)

    ready = (
        postgres_connected and app_attest_connected and migration_ok and litellm_ready
    )

    body = {
        "status": "connected" if ready else "degraded",
        "mlpa_version": mlpa_version,
        "pg_server_dbs": {
            "postgres": "connected" if postgres_connected else "offline",
            "app_attest": "connected" if app_attest_connected else "offline",
        },
        "migration": {
            "expected": sorted(expected),
            "current": sorted(current),
        },
        "litellm": litellm_body,
    }

    if ready:
        return body
    return JSONResponse(status_code=503, content=body)
