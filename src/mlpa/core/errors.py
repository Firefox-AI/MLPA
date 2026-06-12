import json
from dataclasses import dataclass

from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_INVALID_MODEL_NAME,
    ERROR_CODE_INVALID_REQUEST,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    ERROR_CODE_REQUEST_TOO_LARGE,
    ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED,
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
        # SIGNUP_CAP_EXCEEDED is recorded pre-completion, not via classify_upstream_error,
        # so it is not in the mapping below.
        if self.reason == PrometheusRejectionReason.RATE_LIMITED:
            if self.error_code == ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED:
                return AvailabilityReason.RATE_LIMITED_UPSTREAM
            return AvailabilityReason.RATE_LIMITED_OWN
        return _REJECTION_TO_AVAILABILITY_REASON[self.reason]


_RATE_LIMIT_REJECTION: dict[int, tuple[PrometheusRejectionReason, str, str]] = {
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED: (
        PrometheusRejectionReason.BUDGET_EXCEEDED,
        "86400",
        "Budget limit exceeded",
    ),
    ERROR_CODE_RATE_LIMIT_EXCEEDED: (
        PrometheusRejectionReason.RATE_LIMITED,
        "60",
        "Rate limit exceeded",
    ),
    ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED: (
        PrometheusRejectionReason.RATE_LIMITED,
        "60",
        "Upstream rate limit exceeded",
    ),
}


def _parse_rate_limit_error(error_text: str) -> int | None:
    if not error_text:
        return None
    try:
        error_data = json.loads(error_text)
        if is_rate_limit_error(error_data, ["budget"]):
            return ERROR_CODE_BUDGET_LIMIT_EXCEEDED
        if is_rate_limit_error(error_data, ["rate"]):
            return ERROR_CODE_RATE_LIMIT_EXCEEDED
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        pass
    if is_litellm_upstream_rate_limit(error_text):
        return ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED
    return None


def classify_upstream_error(
    *,
    error_text: str,
    status_code: int,
    user: str,
) -> RejectionMatch | None:
    if status_code in {400, 429}:
        error_code = _parse_rate_limit_error(error_text)
        if error_code is not None and error_code in _RATE_LIMIT_REJECTION:
            reason, retry_after, log_prefix = _RATE_LIMIT_REJECTION[error_code]
            return RejectionMatch(
                reason=reason,
                error_code=error_code,
                http_status=429,
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
