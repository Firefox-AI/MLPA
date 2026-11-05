import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_INVALID_MODEL_NAME,
    ERROR_CODE_INVALID_REQUEST,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    ERROR_CODE_REQUEST_TOO_LARGE,
    ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED,
    OTARI_HEADER_BUDGET_SCOPE,
    OTARI_HEADER_ERROR_CODE,
    env,
    is_guest_service_type,
)
from mlpa.core.prometheus_metrics import AvailabilityReason, PrometheusRejectionReason
from mlpa.core.utils import (
    is_context_window_error,
    is_invalid_model_name_error,
    is_invalid_request_error,
    is_litellm_upstream_rate_limit,
    is_rate_limit_error,
)

_REJECTION_TO_AVAILABILITY_REASON: dict[
    PrometheusRejectionReason, AvailabilityReason
] = {
    PrometheusRejectionReason.BUDGET_EXCEEDED: AvailabilityReason.BUDGET_EXCEEDED,
    PrometheusRejectionReason.GLOBAL_BUDGET_EXCEEDED: AvailabilityReason.GLOBAL_BUDGET_EXCEEDED,
    PrometheusRejectionReason.GUEST_BUDGET_EXCEEDED: AvailabilityReason.GUEST_BUDGET_EXCEEDED,
    PrometheusRejectionReason.PAYLOAD_TOO_LARGE: AvailabilityReason.PAYLOAD_TOO_LARGE,
    PrometheusRejectionReason.INVALID_MODEL_NAME: AvailabilityReason.INVALID_MODEL_NAME,
    PrometheusRejectionReason.INVALID_REQUEST: AvailabilityReason.INVALID_REQUEST,
}


@dataclass(frozen=True)
class RejectionMatch:
    reason: PrometheusRejectionReason
    error_code: int
    http_status: int
    retry_after: str | None = None
    log_message: str = ""

    def availability_reason(self) -> AvailabilityReason:
        # SIGNUP_CAP_EXCEEDED and GUEST_CAP_EXCEEDED are recorded pre-completion, not via classify_upstream_error,
        # so it is not in the mapping below.
        if self.reason == PrometheusRejectionReason.RATE_LIMITED:
            if self.error_code == ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED:
                return AvailabilityReason.RATE_LIMITED_UPSTREAM
            return AvailabilityReason.RATE_LIMITED_PLATFORM
        return _REJECTION_TO_AVAILABILITY_REASON[self.reason]


_RATE_LIMIT_REJECTION: dict[int, tuple[int, PrometheusRejectionReason, str, str]] = {
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED: (
        429,
        PrometheusRejectionReason.BUDGET_EXCEEDED,
        "86400",
        "Budget limit exceeded",
    ),
    ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED: (
        500,
        PrometheusRejectionReason.GLOBAL_BUDGET_EXCEEDED,
        "300",
        "Global budget limit exceeded",
    ),
    ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED: (
        429,
        PrometheusRejectionReason.GUEST_BUDGET_EXCEEDED,
        # Placeholder until the 429 contract is agreed with the client team
        "3600",
        "Guest budget limit exceeded",
    ),
    ERROR_CODE_RATE_LIMIT_EXCEEDED: (
        429,
        PrometheusRejectionReason.RATE_LIMITED,
        "60",
        "Rate limit exceeded",
    ),
    ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED: (
        429,
        PrometheusRejectionReason.RATE_LIMITED,
        "60",
        "Upstream rate limit exceeded",
    ),
}

_LITELLM_GLOBAL_BUDGET_ERROR = "ExceededBudget: User=default_user_id over budget"
# LiteLLM's virtual key max_budget error. Team/tag budgets add "Team="/"Tag="
# after the "!", end-user budgets say "ExceededBudget: End User=".
_LITELLM_KEY_BUDGET_ERROR = "Budget has been exceeded! Current cost:"


def _parse_rate_limit_error(error_text: str) -> int | None:
    if not error_text:
        return None
    try:
        error_data = json.loads(error_text)
        if is_rate_limit_error(error_data, [_LITELLM_GLOBAL_BUDGET_ERROR]):
            return ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED
        if is_rate_limit_error(error_data, ["budget"]):
            return ERROR_CODE_BUDGET_LIMIT_EXCEEDED
        if is_rate_limit_error(error_data, ["rate"]):
            return ERROR_CODE_RATE_LIMIT_EXCEEDED
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        pass
    if _LITELLM_GLOBAL_BUDGET_ERROR in error_text:
        return ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED
    if is_litellm_upstream_rate_limit(error_text):
        return ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED
    return None


def _rejection(
    error_code: int, user: str, error_text: str, retry_after: str | None = None
) -> RejectionMatch:
    http_status, reason, default_retry_after, log_prefix = _RATE_LIMIT_REJECTION[
        error_code
    ]
    return RejectionMatch(
        reason=reason,
        error_code=error_code,
        http_status=http_status,
        retry_after=retry_after or default_retry_after,
        log_message=f"{log_prefix} for user {user}: {error_text}",
    )


def otari_code(headers: Mapping[str, str] | None, error_text: str = "") -> str | None:
    """Otari's refusal code, from the Otari-Error-Code header or the body's "code"."""
    code = (headers or {}).get(OTARI_HEADER_ERROR_CODE)
    if code:
        return code
    try:
        body = json.loads(error_text) if error_text else None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    code = body.get("code") if isinstance(body, dict) else None
    return code if isinstance(code, str) else None


def classify_otari_stream_error(event: Any, user: str) -> RejectionMatch | None:
    """A coded Otari stream error event (``{"error": {"code": ...}}``) as MLPA's rejection, or None."""
    error = event.get("error") if isinstance(event, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if not isinstance(code, str):
        return None
    return _classify_otari_error(
        error_code=code, headers={}, error_text=json.dumps(event), user=user
    )


def _classify_otari_error(
    *, error_code: str, headers: Mapping[str, str], error_text: str, user: str
) -> RejectionMatch | None:
    """Map Otari's stable Otari-Error-Code to MLPA's error codes; no text matching."""
    if error_code == "budget_exceeded":
        # The end user's own budget is MLPA's per-user budget; any other scope
        # (the service key's ceiling, the workspace) is the global one, or for a
        # guest the guest key's ceiling: the pool every guest shares. Otari names
        # both ceilings' scope alike, so the service type tells them apart.
        if headers.get(OTARI_HEADER_BUDGET_SCOPE) == "user":
            # Otari's Retry-After is the wait until the refusing budget resets.
            return _rejection(
                ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
                user,
                error_text,
                headers.get("retry-after"),
            )
        if is_guest_service_type(user.partition(":")[2]):
            return _rejection(ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED, user, error_text)
        return _rejection(ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED, user, error_text)
    if error_code == "rate_limited":
        return _rejection(
            ERROR_CODE_RATE_LIMIT_EXCEEDED, user, error_text, headers.get("retry-after")
        )
    if error_code == "upstream_rate_limited":
        return _rejection(
            ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED,
            user,
            error_text,
            headers.get("retry-after"),
        )
    if error_code == "context_length_exceeded":
        return RejectionMatch(
            reason=PrometheusRejectionReason.PAYLOAD_TOO_LARGE,
            error_code=ERROR_CODE_REQUEST_TOO_LARGE,
            http_status=413,
            log_message=f"Context window exceeded for user {user}: {error_text}",
        )
    if error_code in {"invalid_model", "model_not_allowed"}:
        return RejectionMatch(
            reason=PrometheusRejectionReason.INVALID_MODEL_NAME,
            error_code=ERROR_CODE_INVALID_MODEL_NAME,
            http_status=400,
            log_message=f"Invalid model name for user {user}: {error_text}",
        )
    if error_code == "invalid_request":
        return RejectionMatch(
            reason=PrometheusRejectionReason.INVALID_REQUEST,
            error_code=ERROR_CODE_INVALID_REQUEST,
            http_status=400,
            log_message=f"Invalid request for user {user}: {error_text}",
        )
    return None


def classify_upstream_error(
    *,
    error_text: str,
    status_code: int,
    user: str,
    headers: Mapping[str, str] | None = None,
) -> RejectionMatch | None:
    otari_error_code = otari_code(headers, error_text)
    if otari_error_code:
        match = _classify_otari_error(
            error_code=otari_error_code,
            headers=headers or {},
            error_text=error_text,
            user=user,
        )
        if match is not None:
            return match
    if status_code in {400, 429}:
        error_code = _parse_rate_limit_error(error_text)
        # Guests run on MLPA_GUEST_VIRTUAL_KEY, so its budget running out means
        # the shared guest pool is gone, not this guest's own budget.
        if (
            error_code == ERROR_CODE_BUDGET_LIMIT_EXCEEDED
            and user.endswith("-guest")
            and _LITELLM_KEY_BUDGET_ERROR in error_text
        ):
            error_code = ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED
        if error_code is not None and error_code in _RATE_LIMIT_REJECTION:
            http_status, reason, retry_after, log_prefix = _RATE_LIMIT_REJECTION[
                error_code
            ]
            return RejectionMatch(
                reason=reason,
                error_code=error_code,
                http_status=http_status,
                retry_after=retry_after,
                log_message=f"{log_prefix} for user {user}: {error_text}",
            )
    if status_code == 413 or is_context_window_error(error_text):
        return RejectionMatch(
            reason=PrometheusRejectionReason.PAYLOAD_TOO_LARGE,
            error_code=ERROR_CODE_REQUEST_TOO_LARGE,
            http_status=413,
            log_message=f"Context window exceeded for user {user}: {error_text}",
        )
    if status_code == 400:
        if is_invalid_model_name_error(error_text):
            return RejectionMatch(
                reason=PrometheusRejectionReason.INVALID_MODEL_NAME,
                error_code=ERROR_CODE_INVALID_MODEL_NAME,
                http_status=400,
                log_message=f"Invalid model name for user {user}: {error_text}",
            )
        if is_invalid_request_error(error_text):
            return RejectionMatch(
                reason=PrometheusRejectionReason.INVALID_REQUEST,
                error_code=ERROR_CODE_INVALID_REQUEST,
                http_status=400,
                log_message=f"Invalid request for user {user}: {error_text}",
            )
    return None


USER_BLOCKED_DETAIL = {"error": "User is blocked."}


def is_otari_user_blocked(headers: Mapping[str, str] | None) -> bool:
    """Whether Otari refused because the end user is blocked.

    MLPA checks a LiteLLM user's blocked flag before the call; with Otari that
    check rides on the call itself, so the refusal is answered the same way here.
    """
    return otari_code(headers) == "user_blocked"
