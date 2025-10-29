from typing import Optional

from pydantic import BaseModel

from proxy.core.config import env


class ChatRequest(BaseModel):
	stream: Optional[bool] = False
	messages: list[dict] = []
	model: Optional[str] = env.MODEL_NAME
	temperature: Optional[float] = env.TEMPERATURE
	max_completion_tokens: Optional[int] = env.MAX_COMPLETION_TOKENS
	top_p: Optional[float] = env.TOP_P


class UserUpdatePayload(BaseModel):
	user_id: str
	alias: str | None = None
	budget_id: str | None = None
	blocked: bool | None = None


class AttestationRequest(BaseModel):
	key_id: str
	challenge_b64: str
	attestation_obj_b64: str


class AssertionRequest(ChatRequest):
	key_id: str
	challenge_b64: str
	assertion_obj_b64: str


class AuthorizedChatRequest(ChatRequest):
	user: str
