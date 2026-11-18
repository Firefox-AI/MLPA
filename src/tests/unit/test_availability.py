import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_INVALID_MODEL_NAME,
    ERROR_CODE_INVALID_REQUEST,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    ERROR_CODE_REQUEST_TOO_LARGE,
    ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED,
    env,
)
from mlpa.core.errors import (
    RejectionMatch,
    classify_upstream_error,
    seconds_until_budget_reset,
)
from mlpa.core.prometheus_metrics import (
    AvailabilityOutcome,
    AvailabilityReason,
    PrometheusRejectionReason,
    availability_outcome_for,
)


def test_every_availability_reason_maps_to_an_outcome():
    """Guard: a new AvailabilityReason cannot ship without an outcome mapping.

    Future pre-completion reasons added to AvailabilityReason must extend the
    map too; this fails loudly (KeyError) if the map is not updated alongside the enum.
    """
    for reason in AvailabilityReason:
        assert isinstance(availability_outcome_for(reason), AvailabilityOutcome)


# SIGNUP_CAP_EXCEEDED and GUEST_CAP_EXCEEDED are recorded pre-completion, not by
# classify_upstream_error, so they are intentionally outside the completion-stage availability mapping.
_PRE_COMPLETION_REJECTION_REASONS = {
    PrometheusRejectionReason.SIGNUP_CAP_EXCEEDED,
    PrometheusRejectionReason.GUEST_CAP_EXCEEDED,
}
_COMPLETION_STAGE_REJECTION_OUTCOME_OVERRIDES = {
    PrometheusRejectionReason.GLOBAL_BUDGET_EXCEEDED: AvailabilityOutcome.FAILURE
}


def test_every_completion_stage_rejection_reason_maps_to_expected_outcome():
    """Guard: every rejection reason classify_upstream_error can produce must
    resolve through availability_reason() to its expected outcome.

    Iterating the enum (minus the pre-completion reasons) means a newly added
    completion-stage rejection reason fails loudly here until it is mapped, or is
    explicitly classified as pre-completion.
    """
    for reason in PrometheusRejectionReason:
        if reason in _PRE_COMPLETION_REJECTION_REASONS:
            continue
        match = RejectionMatch(reason=reason, error_code=0, http_status=400)
        expected_outcome = _COMPLETION_STAGE_REJECTION_OUTCOME_OVERRIDES.get(
            reason, AvailabilityOutcome.EXCLUDED
        )
        assert availability_outcome_for(match.availability_reason()) == expected_outcome


# Pins the expected availability reason for each completion-stage rejection,
# including the own-vs-upstream rate-limit split keyed on error_code. This fixes
# the exact mappings; the completeness test above guards that the map covers
# every completion-stage rejection reason.
@pytest.mark.parametrize(
    ("reason", "error_code", "expected"),
    [
        (
            PrometheusRejectionReason.BUDGET_EXCEEDED,
            ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
            AvailabilityReason.BUDGET_EXCEEDED,
        ),
        (
            PrometheusRejectionReason.GLOBAL_BUDGET_EXCEEDED,
            ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED,
            AvailabilityReason.GLOBAL_BUDGET_EXCEEDED,
        ),
        (
            PrometheusRejectionReason.GUEST_BUDGET_EXCEEDED,
            ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED,
            AvailabilityReason.GUEST_BUDGET_EXCEEDED,
        ),
        (
            PrometheusRejectionReason.RATE_LIMITED,
            ERROR_CODE_RATE_LIMIT_EXCEEDED,
            AvailabilityReason.RATE_LIMITED_PLATFORM,
        ),
        (
            PrometheusRejectionReason.RATE_LIMITED,
            ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED,
            AvailabilityReason.RATE_LIMITED_UPSTREAM,
        ),
        (
            PrometheusRejectionReason.PAYLOAD_TOO_LARGE,
            ERROR_CODE_REQUEST_TOO_LARGE,
            AvailabilityReason.PAYLOAD_TOO_LARGE,
        ),
        (
            PrometheusRejectionReason.INVALID_MODEL_NAME,
            ERROR_CODE_INVALID_MODEL_NAME,
            AvailabilityReason.INVALID_MODEL_NAME,
        ),
        (
            PrometheusRejectionReason.INVALID_REQUEST,
            ERROR_CODE_INVALID_REQUEST,
            AvailabilityReason.INVALID_REQUEST,
        ),
    ],
)
def test_rejection_match_availability_reason(reason, error_code, expected):
    match = RejectionMatch(reason=reason, error_code=error_code, http_status=400)
    assert match.availability_reason() == expected
    expected_outcome = _COMPLETION_STAGE_REJECTION_OUTCOME_OVERRIDES.get(
        reason, AvailabilityOutcome.EXCLUDED
    )
    assert availability_outcome_for(expected) == expected_outcome


def _litellm_budget_error(message: str) -> str:
    return json.dumps(
        {"error": {"message": message, "type": "budget_exceeded", "code": "400"}}
    )


# Message LiteLLM raises when a virtual key hits its max_budget.
_KEY_BUDGET = "Budget has been exceeded! Current cost: 10.5, Max budget: 10.0"
_END_USER_BUDGET = (
    "ExceededBudget: End User=abc:ai-guest over budget. Spend=0.2, Budget=0.1"
)


@pytest.mark.parametrize(
    ("user", "message", "error_code", "retry_after"),
    [
        # guest key ran out → shared guest pool exhausted
        ("abc:ai-guest", _KEY_BUDGET, ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED, "3600"),
        # guest hit their own budget → stays code 1
        ("abc:ai-guest", _END_USER_BUDGET, ERROR_CODE_BUDGET_LIMIT_EXCEEDED, None),
        # FxA user on MLPA_VIRTUAL_KEY → unchanged behavior
        ("abc:ai", _KEY_BUDGET, ERROR_CODE_BUDGET_LIMIT_EXCEEDED, None),
    ],
)
def test_classify_guest_key_budget(user, message, error_code, retry_after):
    match = classify_upstream_error(
        error_text=_litellm_budget_error(message), status_code=400, user=user
    )
    assert match is not None
    assert match.error_code == error_code
    assert match.http_status == 429
    if retry_after is None:  # budget: seconds until midnight in LITELLM_TIMEZONE
        assert 0 < int(match.retry_after) <= 91200
    else:
        assert match.retry_after == retry_after


_ET = ZoneInfo("US/Eastern")


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # reset target is 00:20 ET (midnight + 20 min buffer)
        (datetime(2026, 11, 17, 23, 59, 30, tzinfo=_ET), "1230"),
        # inside the buffer: wait for today's 00:20, not tomorrow's
        (datetime(2026, 11, 17, 0, 0, 0, tzinfo=_ET), "1200"),
        (datetime(2026, 11, 17, 0, 19, 59, tzinfo=_ET), "1"),
        # buffer over → next day's 00:20
        (datetime(2026, 11, 17, 0, 20, 0, tzinfo=_ET), "86400"),
        # 13:00 UTC = 08:00 EST → 16h20m
        (datetime(2026, 11, 17, 13, 0, 0, tzinfo=timezone.utc), "58800"),
        # DST ends 2026-11-01: that day is 25h long
        (datetime(2026, 11, 1, 0, 20, 0, tzinfo=_ET), "90000"),
        # DST starts 2026-03-08: that day is 23h long
        (datetime(2026, 3, 8, 0, 20, 0, tzinfo=_ET), "82800"),
    ],
)
def test_seconds_until_budget_reset(mocker, now, expected):
    mocker.patch.object(env, "LITELLM_TIMEZONE", "US/Eastern")
    assert seconds_until_budget_reset(now) == expected
