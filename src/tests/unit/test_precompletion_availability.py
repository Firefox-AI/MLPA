"""Pre-completion availability instrumentation.

Covers the dispositions the completion-stage counter cannot see: the chat auth
dependency (service-type / model, auth rejection, client-error auth request, and
the statuses that are intentionally not recorded) and the route-body sites
(signup cap, provisioning failure, blocked). The completion tests bypass the auth
dependency, so the wrapper behavior is only exercised here.
"""

import pytest
from fastapi import HTTPException, Request

from mlpa import run as run_module
from mlpa.core.auth import authorize as authorize_module
from mlpa.core.classes import (
    AuthorizedChatRequest,
    AuthorizedSearchRequest,
    ChatRequest,
)
from mlpa.core.completions import get_or_create_user_for_completion
from mlpa.core.config import ERROR_CODE_MAX_USERS_REACHED
from mlpa.core.prometheus_metrics import (
    AvailabilityOutcome,
    AvailabilityReason,
)
from tests.consts import SAMPLE_REQUEST

# A model/service-type pair that is valid together, so the wrapper passes its own
# check and reaches the shared auth call.
_VALID_MODEL = "openai/gpt-4o"
_AI = authorize_module.ServiceType.ai


def _make_request() -> Request:
    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {"type": "http", "method": "POST", "path": "/", "headers": []}, receive
    )


def _chat_request(model: str = _VALID_MODEL) -> ChatRequest:
    return ChatRequest(model=model, messages=[{"role": "user", "content": "hi"}])


def _availability(
    spy,
    outcome: AvailabilityOutcome,
    reason: AvailabilityReason,
    *,
    model: str,
    service_type: str,
    purpose: str = "",
) -> float:
    return spy.value(
        "chat_availability",
        outcome=outcome,
        reason=reason,
        model=model,
        service_type=service_type,
        purpose=purpose,
    )


def _availability_total(spy) -> float:
    """Sum of every chat_availability sample. Proves exactly one disposition."""
    return sum(
        s.value for s in spy.samples("chat_availability") if s.name.endswith("_total")
    )


def _rejection_total(spy) -> float:
    return sum(
        s.value
        for s in spy.samples("chat_request_rejections")
        if s.name.endswith("_total")
    )


# --- auth dependency (authorize_chat_request) ---------------------------------


async def test_wrapper_success_records_no_auth_stage_availability(mocker, metrics_spy):
    mocker.patch.object(
        authorize_module,
        "fxa_auth",
        mocker.AsyncMock(return_value={"user": "user-123"}),
    )

    result = await authorize_module.authorize_chat_request(
        request=_make_request(),
        chat_request=_chat_request(),
        authorization="Bearer token",
        service_type=_AI,
        purpose="chat",
    )

    assert isinstance(result, AuthorizedChatRequest)
    # Auth success is finalized later at completion, never at the auth stage.
    assert "chat_availability" not in metrics_spy.touched()


async def test_wrapper_invalid_service_type_records_excluded(metrics_spy):
    with pytest.raises(HTTPException) as exc_info:
        await authorize_module.authorize_chat_request(
            request=_make_request(),
            chat_request=ChatRequest(model="exa", messages=[]),
            authorization="Bearer token",
            service_type=_AI,
            purpose="chat",
        )

    assert exc_info.value.status_code == 400
    assert (
        _availability(
            metrics_spy,
            AvailabilityOutcome.EXCLUDED,
            AvailabilityReason.INVALID_SERVICE_TYPE_FOR_MODEL,
            model="exa",
            service_type="ai",
            purpose="chat",
        )
        == 1
    )
    assert _availability_total(metrics_spy) == 1


async def test_wrapper_invalid_purpose_records_invalid_auth_request(metrics_spy):
    # A real shared-call 400 from purpose validation maps to the coarse reason.
    with pytest.raises(HTTPException) as exc_info:
        await authorize_module.authorize_chat_request(
            request=_make_request(),
            chat_request=_chat_request(),
            authorization="Bearer token",
            service_type=_AI,
            purpose="definitely-not-a-valid-purpose",
        )

    assert exc_info.value.status_code == 400
    assert (
        _availability(
            metrics_spy,
            AvailabilityOutcome.EXCLUDED,
            AvailabilityReason.INVALID_AUTH_REQUEST,
            model=_VALID_MODEL,
            service_type="ai",
        )
        == 1
    )
    assert _availability_total(metrics_spy) == 1


async def test_wrapper_shared_call_401_records_auth_rejected(mocker, metrics_spy):
    raised = HTTPException(status_code=401, detail="Invalid FxA auth")
    mocker.patch.object(
        authorize_module,
        "_authorize_common_request",
        mocker.AsyncMock(side_effect=raised),
    )

    with pytest.raises(HTTPException) as exc_info:
        await authorize_module.authorize_chat_request(
            request=_make_request(),
            chat_request=_chat_request(),
            authorization="Bearer token",
            service_type=_AI,
            purpose="chat",
        )

    assert exc_info.value is raised  # re-raised unchanged
    assert (
        _availability(
            metrics_spy,
            AvailabilityOutcome.EXCLUDED,
            AvailabilityReason.AUTH_REJECTED,
            model=_VALID_MODEL,
            service_type="ai",
        )
        == 1
    )
    assert _availability_total(metrics_spy) == 1


async def test_wrapper_shared_call_400_records_invalid_auth_request(
    mocker, metrics_spy
):
    # Pins that the wrapper maps any shared-call 400 to invalid_auth_request,
    # regardless of source. The non-purpose source (malformed App Attest base64
    # decoded in app_attest_auth, which raises before its try) was confirmed by
    # code inspection; this test proves the mapping, not that path's reachability.
    raised = HTTPException(status_code=400, detail={"challenge_b64": "Invalid Base64"})
    mocker.patch.object(
        authorize_module,
        "_authorize_common_request",
        mocker.AsyncMock(side_effect=raised),
    )

    with pytest.raises(HTTPException) as exc_info:
        await authorize_module.authorize_chat_request(
            request=_make_request(),
            chat_request=_chat_request(),
            authorization="Bearer token",
            service_type=_AI,
            purpose="chat",
        )

    assert exc_info.value is raised  # re-raised unchanged
    assert (
        _availability(
            metrics_spy,
            AvailabilityOutcome.EXCLUDED,
            AvailabilityReason.INVALID_AUTH_REQUEST,
            model=_VALID_MODEL,
            service_type="ai",
        )
        == 1
    )
    assert _availability_total(metrics_spy) == 1


async def test_wrapper_shared_call_500_records_nothing(mocker, metrics_spy):
    # App Attest's explicit 500 is an HTTPException, so it is caught, but the
    # wrapper records only 401/400 and re-raises everything else unrecorded.
    raised = HTTPException(
        status_code=500, detail="Server error during App Attest auth"
    )
    mocker.patch.object(
        authorize_module,
        "_authorize_common_request",
        mocker.AsyncMock(side_effect=raised),
    )

    with pytest.raises(HTTPException) as exc_info:
        await authorize_module.authorize_chat_request(
            request=_make_request(),
            chat_request=_chat_request(),
            authorization="Bearer token",
            service_type=_AI,
            purpose="chat",
        )

    assert exc_info.value is raised  # re-raised unchanged
    assert "chat_availability" not in metrics_spy.touched()


async def test_wrapper_shared_call_non_http_exception_records_nothing(
    mocker, metrics_spy
):
    # Non-HTTPException auth-path errors are not caught and propagate unrecorded.
    raised = RuntimeError("bare auth-path failure")
    mocker.patch.object(
        authorize_module,
        "_authorize_common_request",
        mocker.AsyncMock(side_effect=raised),
    )

    with pytest.raises(RuntimeError):
        await authorize_module.authorize_chat_request(
            request=_make_request(),
            chat_request=_chat_request(),
            authorization="Bearer token",
            service_type=_AI,
            purpose="chat",
        )

    assert "chat_availability" not in metrics_spy.touched()


# --- route body: get_or_create_user_for_completion ----------------------------


async def test_signup_cap_records_excluded_alongside_rejection(mocker, metrics_spy):
    mocker.patch(
        "mlpa.core.completions.get_or_create_user",
        mocker.AsyncMock(
            side_effect=HTTPException(
                status_code=403, detail={"error": ERROR_CODE_MAX_USERS_REACHED}
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_or_create_user_for_completion(SAMPLE_REQUEST.user, SAMPLE_REQUEST)

    assert exc_info.value.status_code == 403
    assert (
        _availability(
            metrics_spy,
            AvailabilityOutcome.EXCLUDED,
            AvailabilityReason.SIGNUP_CAP_EXCEEDED,
            model=SAMPLE_REQUEST.model,
            service_type=SAMPLE_REQUEST.service_type,
            purpose=SAMPLE_REQUEST.purpose,
        )
        == 1
    )
    assert _availability_total(metrics_spy) == 1
    # The existing rejection metric is still recorded.
    assert _rejection_total(metrics_spy) == 1


async def test_provisioning_failure_records_failure(mocker, metrics_spy):
    mocker.patch(
        "mlpa.core.completions.get_or_create_user",
        mocker.AsyncMock(
            side_effect=HTTPException(
                status_code=500, detail={"error": "Error fetching user info"}
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_or_create_user_for_completion(SAMPLE_REQUEST.user, SAMPLE_REQUEST)

    assert exc_info.value.status_code == 500
    assert (
        _availability(
            metrics_spy,
            AvailabilityOutcome.FAILURE,
            AvailabilityReason.PROVISIONING_FAILURE,
            model=SAMPLE_REQUEST.model,
            service_type=SAMPLE_REQUEST.service_type,
            purpose=SAMPLE_REQUEST.purpose,
        )
        == 1
    )
    assert _availability_total(metrics_spy) == 1


async def test_non_signup_non_5xx_records_nothing(mocker, metrics_spy):
    # The strict gate leaves a non-signup-cap, non-5xx disposition unrecorded so a
    # client-side 4xx is not counted as an availability failure.
    mocker.patch(
        "mlpa.core.completions.get_or_create_user",
        mocker.AsyncMock(
            side_effect=HTTPException(
                status_code=400, detail={"error": "Invalid user_id format"}
            )
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_or_create_user_for_completion(SAMPLE_REQUEST.user, SAMPLE_REQUEST)

    assert exc_info.value.status_code == 400
    assert "chat_availability" not in metrics_spy.touched()


async def test_search_request_records_no_chat_availability(mocker, metrics_spy):
    search_req = AuthorizedSearchRequest(
        user="user-1:search", service_type="search", query="q", max_results=2
    )
    mocker.patch(
        "mlpa.core.completions.get_or_create_user",
        mocker.AsyncMock(
            side_effect=HTTPException(
                status_code=500, detail={"error": "Error fetching user info"}
            )
        ),
    )

    with pytest.raises(HTTPException):
        await get_or_create_user_for_completion(search_req.user, search_req)

    assert "chat_availability" not in metrics_spy.touched()


# --- route body: blocked user -------------------------------------------------


async def test_blocked_user_records_blocked(mocker, metrics_spy):
    mocker.patch.object(
        run_module,
        "get_or_create_user_for_completion",
        mocker.AsyncMock(return_value=({"blocked": True}, False)),
    )

    with pytest.raises(HTTPException) as exc_info:
        await run_module.chat_completion(
            request=_make_request(),
            authorized_chat_request=SAMPLE_REQUEST,
        )

    assert exc_info.value.status_code == 403
    assert (
        _availability(
            metrics_spy,
            AvailabilityOutcome.EXCLUDED,
            AvailabilityReason.BLOCKED,
            model=SAMPLE_REQUEST.model,
            service_type=SAMPLE_REQUEST.service_type,
            purpose=SAMPLE_REQUEST.purpose,
        )
        == 1
    )
    assert _availability_total(metrics_spy) == 1
