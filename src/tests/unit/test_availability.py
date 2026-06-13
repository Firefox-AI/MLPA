import pytest

from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_INVALID_MODEL_NAME,
    ERROR_CODE_INVALID_REQUEST,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    ERROR_CODE_REQUEST_TOO_LARGE,
    ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED,
)
from mlpa.core.errors import RejectionMatch
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


# SIGNUP_CAP_EXCEEDED is recorded pre-completion, not by classify_upstream_error,
# so it is intentionally outside the completion-stage availability mapping.
_PRE_COMPLETION_REJECTION_REASONS = {PrometheusRejectionReason.SIGNUP_CAP_EXCEEDED}


def test_every_completion_stage_rejection_reason_maps_to_excluded():
    """Guard: every rejection reason classify_upstream_error can produce must
    resolve through availability_reason() to an excluded outcome.

    Iterating the enum (minus the pre-completion reasons) means a newly added
    completion-stage rejection reason fails loudly here until it is mapped, or is
    explicitly classified as pre-completion.
    """
    for reason in PrometheusRejectionReason:
        if reason in _PRE_COMPLETION_REJECTION_REASONS:
            continue
        match = RejectionMatch(reason=reason, error_code=0, http_status=400)
        assert (
            availability_outcome_for(match.availability_reason())
            == AvailabilityOutcome.EXCLUDED
        )


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
            PrometheusRejectionReason.RATE_LIMITED,
            ERROR_CODE_RATE_LIMIT_EXCEEDED,
            AvailabilityReason.RATE_LIMITED_OWN,
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
    # All policy rejections are excluded from the availability ratio.
    assert availability_outcome_for(expected) == AvailabilityOutcome.EXCLUDED
