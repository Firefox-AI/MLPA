import enum
from dataclasses import dataclass
from typing import Optional

from pydantic import BaseModel, Field

from mlpa.core.config import env


class ChatRequest(BaseModel):
    # NOTE: this are sanitized parames we are willing to expose to the user
    # full list is https://docs.litellm.ai/docs/completion/input#input-params-1
    model: str
    stream: Optional[bool] = False
    messages: list[dict] = []
    temperature: Optional[float] = env.TEMPERATURE
    max_completion_tokens: Optional[int] = env.MAX_COMPLETION_TOKENS
    top_p: Optional[float] = env.TOP_P
    mock_response: Optional[str] = None
    mock_timeout: Optional[bool] = None
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
    # exa search params
    text: Optional[bool] = None


class UserUpdatePayload(BaseModel):
    user_id: str
    alias: str | None = None
    budget_id: str | None = None
    blocked: bool | None = None


class BudgetUpdatePayload(BaseModel):
    """Payload for updating a user's budget tier."""

    service_type: str


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
    package_name: str = env.PLAY_INTEGRITY_PACKAGE_NAME


class PlayIntegrityTokenResponse(BaseModel):
    """Short-lived MLPA access token issued after Play Integrity verification."""

    access_token: str
    token_type: str
    expires_in: int


class AuthorizedRequestLogMixin:
    """Shared structured log fields for authorized requests.

    Bound into the loguru contextvar via ``logger.contextualize(**log_fields)``
    in the proxy handlers so every log line emitted while serving the request
    (including mid-stream errors) carries them as queryable ``record.extra.*``
    fields, rather than concatenated into the message string.
    """

    user: str
    service_type: str
    purpose: str

    @property
    def log_fields(self) -> dict[str, str]:
        # `model` only exists on chat requests, not search requests.
        fields = {
            "user": self.user,
            "service_type": self.service_type,
            "purpose": self.purpose or "-",
        }
        model = getattr(self, "model", None)
        if model:
            fields["model"] = model
        return fields


class AuthorizedChatRequest(ChatRequest, AuthorizedRequestLogMixin):
    user: str
    service_type: str
    purpose: str = (
        ""  # From header; empty for service types without defined purposes (e.g. s2s)
    )


class SearchRequest(BaseModel):
    query: str
    max_results: int = Field(ge=1, le=10)


class AuthorizedSearchRequest(SearchRequest, AuthorizedRequestLogMixin):
    user: str
    service_type: str
    purpose: str = (
        ""  # From header; empty for service types without defined purposes (e.g. s2s)
    )


# Dynamically create ServiceType enum from config
ServiceType = enum.Enum("ServiceType", [(st, st) for st in env.valid_service_types])


@dataclass(frozen=True)
class LitellmRoutingSnapshot:
    """Parsed LiteLLM proxy response headers for routing / fallback metrics."""

    backend: str
    attempted_fallbacks: int
    attempted_retries: int
    response_duration_ms: float | None
    response_cost_usd: float | None
