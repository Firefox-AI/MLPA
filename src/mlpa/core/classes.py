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
    tools: Optional[list] = None
    tool_choice: Optional[str | dict] = None


class UserUpdatePayload(BaseModel):
    user_id: str
    alias: str | None = None
    budget_id: str | None = None
    blocked: bool | None = None


# iOS App Attest
class ChallengeResponse(BaseModel):
    challenge: str


class AttestSuccessResponse(BaseModel):
    status: str


class AttestationAuth(BaseModel):
    key_id_b64: str
    challenge_b64: str
    attestation_obj_b64: str
    bundle_id: str


class AssertionAuth(BaseModel):
    key_id_b64: str
    challenge_b64: str
    assertion_obj_b64: str
    bundle_id: str


# Google Play Integrity
class PlayIntegrityRequest(BaseModel):
    integrity_token: str
    user_id: str


class AuthorizedChatRequest(ChatRequest):
    user: str
    service_type: str


# Dynamically create ServiceType enum from config
ServiceType = enum.Enum("ServiceType", [(st, st) for st in env.valid_service_types])
