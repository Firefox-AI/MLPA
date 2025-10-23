import asyncio
import os
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from .core.auth.fxa_auth import authorize_request
from .core.classes import AuthorizedChatRequest
from .core.utils import get_or_create_user

router = APIRouter()


@router.post(
	"/chat/completions", description="Mock LiteLLM endpoint with simulated latency."
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

	latency_ms = int(os.getenv("MOCK_LATENCY_MS", "200"))
	await asyncio.sleep(latency_ms / 1000)

	if authorized_chat_request.stream:

		async def mock_stream():
			yield 'data: {"choices":[{"delta":{"content":"mock token 1"}}]}\n\n'
			await asyncio.sleep(0.05)
			yield 'data: {"choices":[{"delta":{"content":"mock token 2"}}]}\n\n'
			await asyncio.sleep(0.05)
			yield "data: [DONE]\n\n"

		return StreamingResponse(mock_stream(), media_type="text/event-stream")

	return {
		"choices": [{"message": {"content": "mock completion response"}}],
		"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
		"model": "mock-gpt",
	}
