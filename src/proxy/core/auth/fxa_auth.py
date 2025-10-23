from typing import Annotated

from fastapi import Header, HTTPException

from .core.classes import AssertionRequest, AuthorizedChatRequest, ChatRequest
from .core.routers.appattest import app_attest_auth
from .core.routers.fxa import fxa_auth


async def authorize_request(
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
