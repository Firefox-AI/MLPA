import time
from contextlib import asynccontextmanager
from typing import Annotated, Optional

import sentry_sdk
import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .core.classes import AssertionRequest, AuthorizedChatRequest, ChatRequest
from .core.completions import get_completion, stream_completion
from .core.config import env
from .core.pg_services.services import app_attest_pg, litellm_pg
from .core.prometheus_metrics import metrics
from .core.routers.appattest import app_attest_auth, appattest_router
from .core.routers.fxa import fxa_auth, fxa_router
from .core.routers.health import health_router
from .core.routers.user import user_router
from .core.utils import get_or_create_user

tags_metadata = [
	{"name": "Health", "description": "Health check endpoints."},
	{"name": "Metrics", "description": "Prometheus metrics endpoints."},
	{
		"name": "App Attest",
		"description": "Endpoints for verifying App Attest payloads.",
	},
	{"name": "LiteLLM", "description": "Endpoints for interacting with LiteLLM."},
]


async def authorize(
	chat_request: AssertionRequest | ChatRequest,
	x_fxa_authorization: Annotated[str | None, Header()] = None,
) -> AuthorizedChatRequest:
	if isinstance(chat_request, AssertionRequest):
		data = await app_attest_auth(chat_request)
		if data:
			if data.get("error"):
				raise HTTPException(status_code=400, detail=data["error"])
			return AuthorizedChatRequest(
				user=chat_request.key_id,  # "user" is key_id for app attest
				**chat_request.model_dump(
					exclude={"key_id", "challenge_b64", "assertion_obj_b64"}
				),
			)
	if x_fxa_authorization:
		fxa_user_id = fxa_auth(x_fxa_authorization)
		if fxa_user_id:
			if fxa_user_id.get("error"):
				raise HTTPException(status_code=401, detail=fxa_user_id["error"])
			return AuthorizedChatRequest(
				user=fxa_user_id["user"],
				**chat_request.model_dump(),
			)
	raise HTTPException(
		status_code=401, detail="Please authenticate with App Attest or FxA."
	)


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


@app.post(
	"/v1/chat/completions",
	tags=["LiteLLM"],
	description="Authorize first using App Attest or FxA. Either pass the x-fxa-authorization header or include the `{key_id, challenge, and assertion_obj}` in the request body for app attest authorization. `payload` is always required and contains the prompt.",
)
async def chat_completion(
	authorized_chat_request: Annotated[
		Optional[AuthorizedChatRequest], Depends(authorize)
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
	uvicorn.run(app, host="0.0.0.0", port=env.PORT, timeout_keep_alive=10)


if __name__ == "__main__":
	main()
