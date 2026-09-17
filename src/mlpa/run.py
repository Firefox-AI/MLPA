import importlib.metadata
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import sentry_sdk
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from mlpa.core.auth.authorize import authorize_chat_request, authorize_search_request
from mlpa.core.classes import AuthorizedChatRequest, AuthorizedSearchRequest
from mlpa.core.completions import (
    get_completion,
    get_or_create_user_for_completion,
    stream_completion,
)
from mlpa.core.config import (
    ERROR_RESPONSES,
    SENSITIVE_FIELDS_TO_SCRUB_FROM_SENTRY,
    env,
)
from mlpa.core.consts.openapi import (
    CHAT_COMPLETION_DESCRIPTION,
    CHAT_COMPLETION_SUCCESS_RESPONSE,
    SEARCH_DESCRIPTION,
    SEARCH_SUCCESS_RESPONSE,
    TAGS_METADATA,
    customize_openapi,
)
from mlpa.core.http_client import close_http_client, get_http_client
from mlpa.core.logger import logger, setup_logger
from mlpa.core.metrics import (
    SEARCH_MODEL,
    record_chat_availability,
    record_request_country,
)
from mlpa.core.middleware import register_middleware
from mlpa.core.middleware.traffic_contract_enforcer import (
    enforce_traffic_contract,
)
from mlpa.core.prometheus_metrics import AvailabilityReason
from mlpa.core.routers.appattest import appattest_router
from mlpa.core.routers.filter import filter_router
from mlpa.core.routers.health import health_router
from mlpa.core.routers.mock import mock_router
from mlpa.core.routers.play import play_router
from mlpa.core.routers.user import user_router
from mlpa.core.search import get_search
from mlpa.core.services.services import app_attest_pg, litellm_pg, redis_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    litellm_connected = False
    app_attest_connected = False
    redis_connected = False
    try:
        get_http_client()
        await litellm_pg.connect()
        litellm_connected = True

        await app_attest_pg.connect()
        app_attest_connected = True

        if env.ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT:
            await redis_service.connect()
            redis_connected = True

        await litellm_pg.create_budget()
        await app_attest_pg.ensure_capacity_state()

        yield
    finally:
        if app_attest_connected:
            await app_attest_pg.disconnect()
        if litellm_connected:
            await litellm_pg.disconnect()
        if redis_connected:
            await redis_service.close()
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
    description="Authenticates and proxies LLM requests through LiteLLM to enact budgets and per-user management.",
    version=importlib.metadata.version("mlpa"),
    docs_url="/api/docs",
    openapi_tags=TAGS_METADATA,
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
app.include_router(user_router, prefix="/user")
app.include_router(filter_router)
app.include_router(mock_router, prefix="/mock")
customize_openapi(app, TAGS_METADATA)

app.mount(
    "/admin",
    StaticFiles(directory=Path(__file__).parent / "admin", html=True),
    name="admin",
)


@app.post(
    "/v1/chat/completions",
    tags=["LiteLLM"],
    description=CHAT_COMPLETION_DESCRIPTION.strip(),
    responses={**CHAT_COMPLETION_SUCCESS_RESPONSE, **ERROR_RESPONSES},
)
async def chat_completion(
    request: Request,
    authorized_chat_request: Annotated[
        AuthorizedChatRequest, Depends(authorize_chat_request)
    ],
):
    record_request_country(
        authorized_chat_request.client_country,
        service_type=authorized_chat_request.service_type,
        model=authorized_chat_request.model,
    )
    await enforce_traffic_contract(request, authorized_chat_request.service_type)
    user_id = authorized_chat_request.user
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "User not found from authorization response."},
        )
    user, _ = await get_or_create_user_for_completion(
        request, user_id, authorized_chat_request
    )
    if user.get("blocked"):
        record_chat_availability(authorized_chat_request, AvailabilityReason.BLOCKED)
        raise HTTPException(status_code=403, detail={"error": "User is blocked."})

    if authorized_chat_request.stream:
        return StreamingResponse(
            stream_completion(request, authorized_chat_request),
            media_type="text/event-stream",
        )
    else:
        return await get_completion(request, authorized_chat_request)


@app.post(
    "/v1/search",
    tags=["LiteLLM"],
    description=SEARCH_DESCRIPTION.strip(),
    responses={**SEARCH_SUCCESS_RESPONSE, **ERROR_RESPONSES},
)
async def search(
    request: Request,
    authorized_search_request: Annotated[
        AuthorizedSearchRequest, Depends(authorize_search_request)
    ],
):
    record_request_country(
        authorized_search_request.client_country,
        service_type=authorized_search_request.service_type,
        model=SEARCH_MODEL,
    )
    if not env.valid_service_type_for_model(
        authorized_search_request.service_type, SEARCH_MODEL
    ):
        raise HTTPException(
            status_code=400,
            detail=f"service-type header must be one of {env.forced_model_service_type_pairs.get(SEARCH_MODEL)}",
        )
    await enforce_traffic_contract(request, authorized_search_request.service_type)
    user_id = authorized_search_request.user
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={"error": "User not found from authorization response."},
        )
    user, _ = await get_or_create_user_for_completion(
        request, user_id, authorized_search_request
    )
    if user.get("blocked"):
        raise HTTPException(status_code=403, detail={"error": "User is blocked."})

    return await get_search(request, authorized_search_request)


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
