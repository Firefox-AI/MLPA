import enum
from typing import Optional

from pydantic import BaseModel

from mlpa.core.config import env


class ChatRequest(BaseModel):
    # NOTE: this are sanitized parames we are willing to expose to the user
    # full list is https://docs.litellm.ai/docs/completion/input#input-params-1
    stream: Optional[bool] = False
    messages: list[dict] = []
    model: Optional[str] = env.MODEL_NAME
    temperature: Optional[float] = env.TEMPERATURE
    max_completion_tokens: Optional[int] = env.MAX_COMPLETION_TOKENS
    top_p: Optional[float] = env.TOP_P
    mock_response: Optional[str] = None
    tools: Optional[list] = None
    tool_choice: Optional[str | dict] = None
    # Optional OpenAI params
    n: Optional[int] = None
    stream_options: Optional[dict] = None
    stop: Optional[str | list[str]] = None
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    logit_bias: Optional[dict] = None
    # openai v1.0+ new params
    response_format: Optional[dict] = None
    seed: Optional[int] = None
    parallel_tool_calls: Optional[bool] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[int] = None


class UserUpdatePayload(BaseModel):
    user_id: str
    alias: str | None = None
    budget_id: str | None = None
    blocked: bool | None = None


# iOS App Attest
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
    package_name: str = "org.mozilla.firefox"


class AuthorizedChatRequest(ChatRequest):
    user: str
    service_type: str


# Dynamically create ServiceType enum from config
ServiceType = enum.Enum("ServiceType", [(st, st) for st in env.valid_service_types])
