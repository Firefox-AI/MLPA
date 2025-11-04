import time
from contextlib import asynccontextmanager
from typing import Annotated, Optional

import sentry_sdk
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from mlpa.core.auth.fxa_auth import authorize_request
from mlpa.core.classes import AuthorizedChatRequest
from mlpa.core.completions import get_completion, stream_completion
from mlpa.core.config import env
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
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    await litellm_pg.connect()
    await app_attest_pg.connect()
    yield
    await litellm_pg.disconnect()
    await app_attest_pg.disconnect()


sentry_sdk.init(dsn=env.SENTRY_DSN, send_default_pii=True)

app = FastAPI(
    title="MLPA",
    description="A proxy to verify App Attest/FxA payloads and proxy requests through LiteLLM.",
    version="1.0.0",
    docs_url="/api/docs",
    openapi_tags=tags_metadata,
    lifespan=lifespan,
)


# run before all requests
@app.middleware("http")
async def instrument_requests(request: Request, call_next):
    """
    Measures request latency, counts total requests, and tracks requests in progress.
    """
    start_time = time.time()
    metrics.in_progress_requests.inc()

    try:
        response = await call_next(request)

        route = request.scope.get("route")
        endpoint = route.path if route else request.url.path

        metrics.request_latency.labels(
            method=request.method, endpoint=endpoint
        ).observe(time.time() - start_time)
        metrics.requests_total.labels(method=request.method, endpoint=endpoint).inc()
        metrics.response_status_codes.labels(status_code=response.status_code).inc()
        return response
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
    description="Authorize first using App Attest or FxA. Either pass the x-fxa-authorization header or include the `{key_id_b64, challenge_b64, and assertion_obj_b64}` in the request body for app attest authorization. `payload` is always required and contains the prompt.",
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


def main():
    uvicorn.run(
        "mlpa.run:app", host="0.0.0.0", port=env.PORT, timeout_keep_alive=10, workers=2
    )


if __name__ == "__main__":
    main()
