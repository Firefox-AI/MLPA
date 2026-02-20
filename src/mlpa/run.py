import json
from contextlib import asynccontextmanager
from typing import Annotated, Optional

import sentry_sdk
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.openapi.utils import get_openapi
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from mlpa.core.auth.authorize import authorize_request
from mlpa.core.classes import AssertionAuth, AttestationAuth, AuthorizedChatRequest
from mlpa.core.completions import get_completion, stream_completion
from mlpa.core.config import (
    RATE_LIMIT_ERROR_RESPONSE,
    SENSITIVE_FIELDS_TO_SCRUB_FROM_SENTRY,
    env,
)
from mlpa.core.http_client import close_http_client, get_http_client
from mlpa.core.logger import logger, setup_logger
from mlpa.core.middleware import register_middleware
from mlpa.core.pg_services.services import app_attest_pg, litellm_pg
from mlpa.core.routers.appattest import appattest_router
from mlpa.core.routers.fxa import fxa_router
from mlpa.core.routers.health import health_router
from mlpa.core.routers.mock import mock_router
from mlpa.core.routers.play import play_router
from mlpa.core.routers.user import user_router
from mlpa.core.utils import get_or_create_user

tags_metadata = [
    {"name": "Health", "description": "Health check endpoints."},
    {"name": "Metrics", "description": "Prometheus metrics endpoints."},
    {
        "name": "App Attest",
        "description": "iOS App Attest verification flow: (1) GET /verify/challenge to obtain a challenge, "
        "(2) POST /verify/attest with a JWT containing the attestation object. "
        "Use the attested key for subsequent requests to /v1/chat/completions with use-app-attest header.",
    },
    {
        "name": "Play Integrity",
        "description": "Endpoints for verifying Play Integrity payloads.",
    },
    {"name": "LiteLLM", "description": "Endpoints for interacting with LiteLLM."},
    {"name": "Mock", "description": "Mock endpoints for testing purposes."},
    {
        "name": "User Management",
        "description": "Endpoints for managing user blocking status.",
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    litellm_connected = False
    app_attest_connected = False
    try:
        get_http_client()
        await litellm_pg.connect()
        litellm_connected = True

        await app_attest_pg.connect()
        app_attest_connected = True

        await litellm_pg.create_budget()

        yield
    finally:
        if app_attest_connected:
            await app_attest_pg.disconnect()
        if litellm_connected:
            await litellm_pg.disconnect()
        await close_http_client()


def sentry_scrub_sensitive_fields(event, hint):
    if "request" in event and "data" in event["request"]:
        try:
            body = event["request"]["data"]
            if isinstance(body, str):
                body = json.loads(body)

            for field in SENSITIVE_FIELDS_TO_SCRUB_FROM_SENTRY:
                if field in body:
                    body[field] = "[Filtered]"

            event["request"]["data"] = body
        except Exception:
            pass

    return event


sentry_sdk.init(
    before_send=sentry_scrub_sensitive_fields,
    dsn=env.SENTRY_DSN,
    send_default_pii=False,
)

app = FastAPI(
    title="MLPA",
    description="A proxy to verify App Attest/FxA payloads and proxy requests through LiteLLM.",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_tags=tags_metadata,
    lifespan=lifespan,
)

# Register all middleware in explicit execution order
# See mlpa.core.middleware.__init__.py for execution order documentation
register_middleware(app)


@app.get("/metrics", tags=["Metrics"])
async def get_metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(health_router, prefix="/health")
app.include_router(appattest_router, prefix="/verify")
app.include_router(play_router, prefix="/verify")
app.include_router(fxa_router, prefix="/fxa")
app.include_router(user_router, prefix="/user")
app.include_router(mock_router, prefix="/mock")


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=tags_metadata,
    )
    schemas = openapi_schema.setdefault("components", {}).setdefault("schemas", {})
    attest_schema = AttestationAuth.model_json_schema()
    attest_schema["description"] = "JWT payload for POST /verify/attest"
    schemas["AttestationAuth"] = attest_schema
    assert_schema = AssertionAuth.model_json_schema()
    assert_schema["description"] = (
        "JWT payload for POST /v1/chat/completions with use-app-attest"
    )
    schemas["AssertionAuth"] = assert_schema
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = _custom_openapi


@app.post(
    "/v1/chat/completions",
    tags=["LiteLLM"],
    description="Authorize first using App Attest or FxA. "
    "For FxA: pass the OAuth token in Authorization. "
    "For App Attest: set use-app-attest header and pass a Bearer JWT in Authorization (see AssertionAuth schema).",
    responses=RATE_LIMIT_ERROR_RESPONSE,
)
async def chat_completion(
    authorized_chat_request: Annotated[
        Optional[AuthorizedChatRequest], Depends(authorize_request)
    ],
):
    user_id = authorized_chat_request.user
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "User not found from authorization response."},
        )
    user, _ = await get_or_create_user(user_id)
    if user.get("blocked"):
        raise HTTPException(status_code=403, detail={"error": "User is blocked."})

    if authorized_chat_request.stream:
        return StreamingResponse(
            stream_completion(authorized_chat_request),
            media_type="text/event-stream",
        )
    else:
        return await get_completion(authorized_chat_request)


@app.exception_handler(HTTPException)
async def log_and_handle_http_exception(request: Request, exc: HTTPException):
    """Logs HTTPExceptions"""
    if exc.status_code != 429:
        logger.error(
            f"HTTPException for {request.method} {request.url.path} -> status={exc.status_code} detail={exc.detail}",
        )
    return await http_exception_handler(request, exc)


def main():
    setup_logger()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=env.PORT,
        timeout_keep_alive=10,
        timeout_graceful_shutdown=60,
        log_config=None,
        log_level=None,
    )


if __name__ == "__main__":
    main()
