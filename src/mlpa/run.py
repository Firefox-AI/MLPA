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

    # Forward non-auth headers to log metadata
    with logger.contextualize(
        service_type=request.headers.get("service-type", "N/A"),
        session_id=request.headers.get("session-id", "N/A"),
        user_agent=request.headers.get("user-agent", "N/A"),
        use_app_attest=request.headers.get("use-app-attest", "N/A"),
        request_source=request.headers.get("x-request-source", "N/A"),
    ):
        path = request.url.path
        try:
            # Capture request size if available
            content_length = request.headers.get("content-length")
            if content_length:
                metrics.request_size_bytes.labels(method=request.method).observe(
                    int(content_length)
                )

                logger.info(
                    "Incoming request size captured",
                    extra={
                        "request_method": request.method,
                        "content_length": int(content_length) if content_length else 0,
                        "path": path,
                    },
                )

            response = await call_next(request)

            duration = time.time() - start_time
            route = request.scope.get("route")
            endpoint = route.path if route else request.url.path

            # Capture response size
            res_content_length = response.headers.get("content-length")
            if res_content_length:
                metrics.response_size_bytes.observe(int(res_content_length))
                logger.info(
                    "Response content length captured",
                    extra={
                        "request_method": request.method,
                        "endpoint": endpoint,
                        "path": path,
                        "response_size_bytes": res_content_length,
                        "status_code": response.status_code,
                        "latency_ms": duration,
                    },
                )
            logger.info(
                "Request finished",
                extra={
                    "request_method": request.method,
                    "endpoint": endpoint,
                    "path": path,
                    "response_size_bytes": res_content_length,
                    "status_code": response.status_code,
                    "latency_ms": duration,
                },
            )

            return response
        except Exception as e:
            metrics.request_error_count_total.labels(
                method=request.method, error_type=type(e).__name__
            ).inc()
            logger.error(
                "Request failed with exception",
                extra={
                    "request_method": request.method,
                    "path": request.url.path,
                    "latency_ms": (time.time() - start_time) * 1000,
                    "error_type": type(e).__name__,
                },
                exc_info=True,  # Provides the stack trace for SRE debugging
            )
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
    user_id = authorized_chat_request.user
    model = authorized_chat_request.model

    logger.info(
        "Chat completion request initiated",
        extra={"user_id": user_id, "model": model},
    )

    if not user_id:
        metrics.ai_error_count_total.labels(
            model_name=model, error=f"UserNotFound"
        ).inc()
        logger.warning(
            "Chat completion failed: User not found",
            extra={"user_id": user_id, "model": model},
        )
        raise HTTPException(
            status_code=400,
            detail={"error": "User not found from authorization response."},
        )
    user, _ = await get_or_create_user(user_id)
    if user.get("blocked"):
        metrics.ai_error_count_total.labels(
            model_name=model, error=f"UserBlocked"
        ).inc()
        logger.warning(
            "Chat completion failed: User blocked",
            extra={"user_id": user_id, "model": model},
        )
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
    metrics.request_error_count_total.labels(
        method=request.method, error_type=f"HTTPError"
    ).inc()
    metrics.response_status_codes.labels(status_code=exc.status_code).inc()

    if exc.status_code != 429:
        logger.error(
            "HTTPException occurred",
            extra={
                "path": request.url.path,
                "method": request.method,
                "status_code": exc.status_code,
            },
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
