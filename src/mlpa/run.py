import time
from contextlib import asynccontextmanager
from typing import Annotated, Optional

import sentry_sdk
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from mlpa.core.auth.authorize import authorize_request
from mlpa.core.classes import AuthorizedChatRequest
from mlpa.core.completions import get_completion, stream_completion
from mlpa.core.config import (
    RATE_LIMIT_ERROR_RESPONSE,
    env,
)
from mlpa.core.logger import logger, setup_logger
from mlpa.core.pg_services.services import app_attest_pg, litellm_pg
from mlpa.core.prometheus_metrics import metrics
from mlpa.core.routers.appattest import appattest_router
from mlpa.core.routers.fxa import fxa_router
from mlpa.core.routers.health import health_router
from mlpa.core.routers.mock import mock_router
from mlpa.core.routers.user import user_router
from mlpa.core.utils import get_or_create_user

tags_metadata = [
    {"name": "Health", "description": "Health check endpoints."},
    {"name": "Metrics", "description": "Prometheus metrics endpoints."},
    {
        "name": "App Attest",
        "description": "Endpoints for verifying App Attest payloads.",
    },
    {"name": "LiteLLM", "description": "Endpoints for interacting with LiteLLM."},
    {"name": "Mock", "description": "Mock endpoints for testing purposes."},
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
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


sentry_sdk.init(dsn=env.SENTRY_DSN, send_default_pii=True)

app = FastAPI(
    title="MLPA",
    description="A proxy to verify App Attest/FxA payloads and proxy requests through LiteLLM.",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_tags=tags_metadata,
    lifespan=lifespan,
)


# Run before all requests
@app.middleware("http")
async def instrument_requests(request: Request, call_next):
    """
    Measures request latency, counts total requests, and tracks requests in progress.
    """
    start_time = time.time()
    metrics.in_progress_requests.inc()

    metrics.request_count_total.labels(method=request.method).inc()

    # Forward non-auth headers to log metadata
    with logger.contextualize(
        service_type=request.headers.get("service-type", "N/A"),
        session_id=request.headers.get("session-id", "N/A"),
        user_agent=request.headers.get("user-agent", "N/A"),
        use_app_attest=request.headers.get("use-app-attest", "N/A"),
    ):
        try:
            # Capture request size if available
            content_length = request.headers.get("content-length")
            if content_length:
                metrics.request_size_bytes.observe(int(content_length))

            response = await call_next(request)

            duration = time.time() - start_time
            route = request.scope.get("route")
            endpoint = route.path if route else request.url.path

            metrics.request_latency.labels(method=request.method).observe(duration)
            metrics.requests_total.labels(method=request.method).inc()
            metrics.response_status_codes.labels(status_code=response.status_code).inc()

            metrics.request_duration_seconds.labels(method=request.method).observe(
                duration
            )

            # Capture response size
            res_content_length = response.headers.get("content-length")
            if res_content_length:
                metrics.response_size_bytes.observe(int(res_content_length))

            return response
        except Exception as e:
            metrics.error_count_total.labels(error_type=type(e).__name__).inc()
            raise e
        finally:
            metrics.in_progress_requests.dec()


@app.get("/metrics", tags=["Metrics"])
async def get_metrics():
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(health_router, prefix="/health")
app.include_router(appattest_router, prefix="/verify")
app.include_router(fxa_router, prefix="/fxa")
app.include_router(user_router, prefix="/user")
app.include_router(mock_router, prefix="/mock")


@app.post(
    "/v1/chat/completions",
    tags=["LiteLLM"],
    description="Authorize first using App Attest or FxA. Pass the authorization header containing either the FxA token or the App Attest data JWT",
    responses=RATE_LIMIT_ERROR_RESPONSE,
)
async def chat_completion(
    authorized_chat_request: Annotated[
        Optional[AuthorizedChatRequest], Depends(authorize_request)
    ],
):
    start_time = time.time()
    if authorized_chat_request is None:
        None

    user_id = authorized_chat_request.user
    if not user_id:
        metrics.error_count_total.labels(error_type=f"UserNotFound").inc()
        raise HTTPException(
            status_code=400,
            detail={"error": "User not found from authorization response."},
        )
    user, _ = await get_or_create_user(user_id)
    if user.get("blocked"):
        metrics.inference_blocked_total.labels(backend="internal_policy").inc()
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
    metrics.error_count_total.labels(error_type=f"HTTP_{exc.status_code}").inc()

    if exc.status_code == 429:
        metrics.auth_rate_limit_dropped_total.inc()
    else:
        logger.error(
            f"HTTPException for {request.method} {request.url.path} -> status={exc.status_code}"
        )
    return await http_exception_handler(request, exc)


def main():
    setup_logger()
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=env.PORT,
        timeout_keep_alive=10,
        log_config=None,
        log_level=None,
    )


if __name__ == "__main__":
    main()
