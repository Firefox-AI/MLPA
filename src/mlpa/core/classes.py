import enum
from typing import Optional

from pydantic import BaseModel

from mlpa.core.config import env


class ChatRequest(BaseModel):
    stream: Optional[bool] = False
    messages: list[dict] = []
    model: Optional[str] = env.MODEL_NAME
    temperature: Optional[float] = env.TEMPERATURE
    max_completion_tokens: Optional[int] = env.MAX_COMPLETION_TOKENS
    top_p: Optional[float] = env.TOP_P
    mock_response: Optional[str] = None


class UserUpdatePayload(BaseModel):
    user_id: str
    alias: str | None = None
    budget_id: str | None = None
    blocked: bool | None = None


class AttestationAuth(BaseModel):
    key_id_b64: str
    challenge_b64: str
    attestation_obj_b64: str


class AssertionAuth(BaseModel):
    key_id_b64: str
    challenge_b64: str
    assertion_obj_b64: str


class AuthorizedChatRequest(ChatRequest):
    user: str


class ServiceType(enum.Enum):
    s2s = "s2s"
    ai = "ai"
