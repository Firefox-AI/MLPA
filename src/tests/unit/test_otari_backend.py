"""GATEWAY_BACKEND=otari: Otari's error codes, and users, budgets and limits through its API."""

import json

import httpx
import pytest
from fastapi import HTTPException

from mlpa.core import config as config_module
from mlpa.core.auth import authorize
from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_INVALID_MODEL_NAME,
    ERROR_CODE_INVALID_REQUEST,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    ERROR_CODE_REQUEST_TOO_LARGE,
    ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED,
    OTARI_API_ROOT,
    env,
    resolve_litellm_virtual_auth_headers,
)
from mlpa.core.errors import (
    classify_otari_stream_error,
    classify_upstream_error,
    is_otari_user_blocked,
)
from mlpa.core.services.otari_service import OtariService


def _classify(code: str, status: int = 429, user: str = "u:ai", **headers: str):
    return classify_upstream_error(
        error_text='{"detail": "reworded at will"}',
        status_code=status,
        user=user,
        headers=httpx.Headers({"Otari-Error-Code": code, **headers}),
    )


@pytest.mark.parametrize(
    ("code", "scope", "status", "expected", "http_status"),
    [
        ("budget_exceeded", "user", 403, ERROR_CODE_BUDGET_LIMIT_EXCEEDED, 429),
        (
            "budget_exceeded",
            "api_token",
            403,
            ERROR_CODE_GLOBAL_BUDGET_LIMIT_EXCEEDED,
            500,
        ),
        ("rate_limited", None, 429, ERROR_CODE_RATE_LIMIT_EXCEEDED, 429),
        (
            "upstream_rate_limited",
            None,
            429,
            ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED,
            429,
        ),
        ("invalid_model", None, 400, ERROR_CODE_INVALID_MODEL_NAME, 400),
        ("invalid_request", None, 422, ERROR_CODE_INVALID_REQUEST, 400),
    ],
)
def test_otari_codes_map_without_reading_the_text(
    code, scope, status, expected, http_status
):
    headers = {"Otari-Budget-Scope": scope} if scope else {}
    match = _classify(code, status, **headers)

    assert match is not None
    assert match.error_code == expected
    assert match.http_status == http_status


def test_otari_retry_after_is_kept():
    match = _classify("rate_limited", **{"Retry-After": "17"})

    assert match is not None
    assert match.retry_after == "17"


def test_a_blocked_end_user_is_recognized():
    assert is_otari_user_blocked(httpx.Headers({"Otari-Error-Code": "user_blocked"}))
    assert not is_otari_user_blocked(httpx.Headers({}))


@pytest.fixture
async def otari():
    service = OtariService()
    await service.connect()
    yield service
    await service.disconnect()


async def test_an_end_user_is_read_by_the_id_mlpa_names_it_by(otari, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=httpx.URL(f"{OTARI_API_ROOT}/end-users", params={"external_id": "fxa1:ai"}),
        json=[
            {
                "user_id": "eu_1",
                "external_id": "fxa1:ai",
                "blocked": True,
                "spend": 0.02,
                "budget_id": "end-user-budget-ai",
            }
        ],
    )

    user = await otari.get_user("fxa1:ai")

    assert user is not None
    assert user["user_id"] == "fxa1:ai"
    assert user["otari_user_id"] == "eu_1"
    assert user["budget_id"] == "end-user-budget-ai"
    assert user["blocked"] is True
    assert otari.is_known("fxa1:ai")


async def test_an_unknown_end_user_is_none(otari, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=httpx.URL(f"{OTARI_API_ROOT}/end-users", params={"external_id": "new:ai"}),
        json=[],
    )

    assert await otari.get_user("new:ai") is None
    assert not otari.is_known("new:ai")


async def test_blocking_and_moving_patch_the_end_user(otari, httpx_mock):
    url = httpx.URL(
        f"{OTARI_API_ROOT}/end-users", params={"external_id": "fxa1:memories"}
    )
    httpx_mock.add_response(
        method="PATCH",
        url=url,
        json={"user_id": "eu_2", "external_id": "fxa1:memories", "blocked": True},
    )
    httpx_mock.add_response(
        method="PATCH",
        url=url,
        json={"user_id": "eu_2", "external_id": "fxa1:memories", "budget_id": "b-dev"},
    )

    blocked = await otari.block_user("fxa1:memories")
    moved = await otari.update_user_budget("fxa1:memories", "b-dev")

    first, second = httpx_mock.get_requests()
    assert blocked["blocked"] is True
    assert json.loads(first.content) == {"blocked": True}
    assert moved["budget_id"] == "b-dev"
    assert json.loads(second.content) == {"budget_id": "b-dev"}


async def test_end_users_are_managed_with_the_service_key_alone(
    otari, httpx_mock, monkeypatch
):
    monkeypatch.setattr(env, "OTARI_SERVICE_KEY", "tk-service")
    await otari.disconnect()
    await otari.connect()
    httpx_mock.add_response(
        method="GET",
        url=httpx.URL(f"{OTARI_API_ROOT}/end-users", params={"external_id": "a:ai"}),
        json=[{"user_id": "eu_a", "external_id": "a:ai"}],
    )

    await otari.get_user("a:ai")

    (request,) = httpx_mock.get_requests()
    assert request.headers["Authorization"] == "Bearer tk-service"


_AI_CONFIG = {
    "ai": {
        "feature": "smart-window",
        "budget_id": "end-user-budget-ai",
        "budget_duration": "1d",
        "max_budget": 0.1,
        "rpm_limit": 40,
        "tpm_limit": 2000,
    }
}


@pytest.fixture
def ai_only(monkeypatch):
    # service_type_config is a cached_property, so the cached value is what to replace.
    monkeypatch.setitem(env.__dict__, "service_type_config", _AI_CONFIG)


async def test_startup_only_reads(otari, httpx_mock, ai_only, mocker):
    error = mocker.patch("mlpa.core.services.otari_service.logger.error")
    httpx_mock.add_response(
        method="GET",
        url=f"{OTARI_API_ROOT}/end-user-budgets",
        json=[{"budget_id": "end-user-budget-ai", "end_user_count": 0}],
    )

    await otari.create_budget()

    assert {r.method for r in httpx_mock.get_requests()} == {"GET"}
    error.assert_not_called()


async def test_startup_names_a_budget_the_key_may_not_assign(
    otari, httpx_mock, ai_only, mocker
):
    error = mocker.patch("mlpa.core.services.otari_service.logger.error")
    httpx_mock.add_response(
        method="GET", url=f"{OTARI_API_ROOT}/end-user-budgets", json=[]
    )

    await otari.create_budget()

    error.assert_called_once()
    assert "end-user-budget-ai" in error.call_args.args[0]


async def test_users_are_counted_by_their_service_types_budget(
    otari, httpx_mock, ai_only
):
    httpx_mock.add_response(
        method="GET",
        url=f"{OTARI_API_ROOT}/end-user-budgets",
        json=[
            {"budget_id": "end-user-budget-ai", "end_user_count": 42},
            {"budget_id": "end-user-budget-dev", "end_user_count": 7},
        ],
    )

    assert await otari.count_users_by_service_type() == {
        "service_type_counts": {"ai": 42},
        "total_users": 42,
    }


async def test_users_are_listed_from_the_keys_end_users(otari, httpx_mock):
    httpx_mock.add_response(
        method="GET",
        url=httpx.URL(
            f"{OTARI_API_ROOT}/end-users",
            params={
                "skip": 10,
                "limit": 2,
                "include_total": "true",
            },
        ),
        json=[{"user_id": "eu_1", "external_id": "a:ai"}],
        headers={"Otari-Total-Count": "11"},
    )

    listed = await otari.list_users(limit=2, offset=10)

    assert listed["total"] == 11
    assert [user["user_id"] for user in listed["users"]] == ["a:ai"]


def test_requests_name_the_service_types_budget(monkeypatch, ai_only):
    monkeypatch.setattr(config_module, "USE_OTARI", True)
    monkeypatch.setattr(env, "OTARI_SERVICE_KEY", "sk-1")

    headers = resolve_litellm_virtual_auth_headers(service_type="ai")

    assert headers["Authorization"] == "Bearer sk-1"
    assert headers["otari-end-user-budget"] == "end-user-budget-ai"


def test_the_code_in_the_body_is_enough():
    match = classify_upstream_error(
        error_text=json.dumps(
            {"detail": "anything", "code": "context_length_exceeded"}
        ),
        status_code=400,
        user="u:ai",
    )

    assert match is not None
    assert match.error_code == ERROR_CODE_REQUEST_TOO_LARGE
    assert match.http_status == 413


def test_a_coded_stream_error_event_maps_to_mlpa_codes():
    event = {
        "error": {
            "message": "x",
            "type": "server_error",
            "code": "upstream_rate_limited",
        }
    }

    match = classify_otari_stream_error(event, "u:ai")

    assert match is not None
    assert match.error_code == ERROR_CODE_UPSTREAM_RATE_LIMIT_EXCEEDED
    assert classify_otari_stream_error({"choices": []}, "u:ai") is None


def _refused(check, header: str) -> bool:
    from fastapi import HTTPException

    try:
        check(header)
    except HTTPException as e:
        return e.status_code == 401
    return False


def test_admin_routes_take_only_mlpa_admin_key_under_otari(monkeypatch):
    from mlpa.core.routers.user.user import require_master_key

    monkeypatch.setattr(config_module, "USE_OTARI", True)
    monkeypatch.setattr(env, "MASTER_KEY", "sk-default")
    monkeypatch.setattr(env, "MLPA_ADMIN_KEY", "")

    assert _refused(require_master_key, "Bearer sk-default")

    monkeypatch.setattr(env, "MLPA_ADMIN_KEY", "sk-admin")

    assert _refused(require_master_key, "Bearer sk-default")
    require_master_key("Bearer sk-admin")


def test_admin_routes_keep_litellms_master_key_under_litellm(monkeypatch):
    from mlpa.core.routers.user.user import require_master_key

    monkeypatch.setattr(config_module, "USE_OTARI", False)
    monkeypatch.setattr(env, "MASTER_KEY", "sk-litellm")
    monkeypatch.setattr(env, "MLPA_ADMIN_KEY", "")

    require_master_key("Bearer sk-litellm")


def test_the_default_ui_access_key_is_refused_under_otari(monkeypatch):
    from mlpa.core.routers.user.user import require_ui_access_key

    monkeypatch.setattr(config_module, "USE_OTARI", True)
    monkeypatch.setattr(env, "MLPA_UI_ACCESS_KEY", "sk-ui-access-default")

    assert _refused(require_ui_access_key, "Bearer sk-ui-access-default")

    monkeypatch.setattr(env, "MLPA_UI_ACCESS_KEY", "sk-ui")

    require_ui_access_key("Bearer sk-ui")


def test_a_users_budget_refusal_retries_after_otaris_retry_after():
    match = _classify(
        "budget_exceeded", 403, **{"Otari-Budget-Scope": "user", "Retry-After": "3600"}
    )

    assert match is not None
    assert match.retry_after == "3600"


async def test_an_app_attest_user_is_named_by_its_own_id(otari, httpx_mock):
    """An App Attest key id often contains "/", so the end user is named in the
    query, never in the path, and under the very id MLPA uses."""
    httpx_mock.add_response(
        method="GET",
        url=httpx.URL(
            f"{OTARI_API_ROOT}/end-users", params={"external_id": "a/b=:s2s"}
        ),
        json=[{"user_id": "eu_1", "external_id": "a/b=:s2s", "blocked": False}],
    )

    user = await otari.get_user("a/b=:s2s")

    assert user is not None
    assert user["user_id"] == "a/b=:s2s"


# Guests: a second service key with the same owner (OTARI_GUEST_SERVICE_KEY).

_GUEST_CONFIG = {
    **_AI_CONFIG,
    "ai-guest": {**_AI_CONFIG["ai"], "budget_id": "end-user-budget-ai-guest"},
}


@pytest.fixture
def ai_and_guest(monkeypatch):
    monkeypatch.setitem(env.__dict__, "service_type_config", _GUEST_CONFIG)
    monkeypatch.setattr(env, "OTARI_SERVICE_KEY", "tk-service")
    monkeypatch.setattr(env, "OTARI_GUEST_SERVICE_KEY", "tk-guest")


def test_the_guest_keys_ceiling_is_the_guest_budget():
    match = _classify(
        "budget_exceeded", 403, user="g:ai-guest", **{"Otari-Budget-Scope": "api_token"}
    )

    assert match is not None
    assert match.error_code == ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED
    assert match.http_status == 429


def test_a_guests_own_budget_is_still_the_per_user_budget():
    match = _classify(
        "budget_exceeded", 403, user="g:ai-guest", **{"Otari-Budget-Scope": "user"}
    )

    assert match is not None
    assert match.error_code == ERROR_CODE_BUDGET_LIMIT_EXCEEDED


def test_a_coded_stream_error_for_a_guest_maps_to_the_guest_budget():
    match = classify_otari_stream_error(
        {"error": {"code": "budget_exceeded"}}, "g:ai-guest"
    )

    assert match is not None
    assert match.error_code == ERROR_CODE_GUEST_BUDGET_LIMIT_EXCEEDED


def test_guests_are_sent_on_the_guest_key_with_the_guest_budget(
    monkeypatch, ai_and_guest
):
    monkeypatch.setattr(config_module, "USE_OTARI", True)

    guest = resolve_litellm_virtual_auth_headers(service_type="ai-guest")
    user = resolve_litellm_virtual_auth_headers(service_type="ai")

    assert guest["Authorization"] == "Bearer tk-guest"
    assert guest["otari-end-user-budget"] == "end-user-budget-ai-guest"
    assert user["Authorization"] == "Bearer tk-service"


def test_guests_never_fall_back_to_the_service_key(monkeypatch, ai_and_guest):
    monkeypatch.setattr(config_module, "USE_OTARI", True)
    monkeypatch.setattr(env, "OTARI_GUEST_SERVICE_KEY", "")

    with pytest.raises(RuntimeError, match="OTARI_GUEST_SERVICE_KEY"):
        resolve_litellm_virtual_auth_headers(service_type="ai-guest")


def test_guest_auth_needs_the_otari_guest_key(monkeypatch):
    monkeypatch.setattr(authorize, "USE_OTARI", True)
    monkeypatch.setattr(env, "MLPA_GUEST_VIRTUAL_KEY", "sk-litellm-guest")
    monkeypatch.setattr(env, "OTARI_GUEST_SERVICE_KEY", "")

    with pytest.raises(HTTPException) as exc_info:
        authorize.guest_virtual_key()
    assert exc_info.value.status_code == 503

    monkeypatch.setattr(env, "OTARI_GUEST_SERVICE_KEY", "tk-guest")
    # None: the headers pick the guest key by service type.
    assert authorize.guest_virtual_key() is None


async def test_guests_are_counted_from_the_guest_keys_budgets(
    otari, httpx_mock, ai_and_guest
):
    httpx_mock.add_response(
        method="GET",
        url=f"{OTARI_API_ROOT}/end-user-budgets",
        match_headers={"Authorization": "Bearer tk-service"},
        json=[{"budget_id": "end-user-budget-ai", "end_user_count": 42}],
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{OTARI_API_ROOT}/end-user-budgets",
        match_headers={"Authorization": "Bearer tk-guest"},
        json=[{"budget_id": "end-user-budget-ai-guest", "end_user_count": 5}],
    )

    assert await otari.count_users_by_service_type() == {
        "service_type_counts": {"ai": 42, "ai-guest": 5},
        "total_users": 47,
    }


async def test_startup_checks_guest_budgets_against_the_guest_key(
    otari, httpx_mock, ai_and_guest, mocker
):
    error = mocker.patch("mlpa.core.services.otari_service.logger.error")
    httpx_mock.add_response(
        method="GET",
        url=f"{OTARI_API_ROOT}/end-user-budgets",
        match_headers={"Authorization": "Bearer tk-service"},
        json=[{"budget_id": "end-user-budget-ai", "end_user_count": 0}],
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{OTARI_API_ROOT}/end-user-budgets",
        match_headers={"Authorization": "Bearer tk-guest"},
        json=[],
    )

    await otari.create_budget()

    error.assert_called_once()
    assert "OTARI_GUEST_SERVICE_KEY" in error.call_args.args[0]
    assert "end-user-budget-ai-guest" in error.call_args.args[0]
