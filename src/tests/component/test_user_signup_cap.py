from unittest.mock import patch

import pytest

from mlpa.core.config import ERROR_CODE_MAX_GUESTS_REACHED, env
from mlpa.core.routers.guest.guest import issue_guest_token
from tests.consts import SAMPLE_REQUEST, SUCCESSFUL_CHAT_RESPONSE, TEST_FXA_TOKEN


@pytest.fixture
def use_real_get_or_create_user():
    # Exercise the real get_or_create_user() implementation instead of the mocked one.
    return True


@pytest.fixture(autouse=True)
def single_fxa_scope(mocker):
    mocker.patch("mlpa.core.auth.fxa.FXA_SCOPES", ("profile:uid",))


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeLiteLLMHTTPClient:
    def __init__(self, litellm_pg_mock):
        self._pg = litellm_pg_mock
        self.fail_next_create = False

    async def get(self, url: str, params=None, headers=None):
        if "customer/info" in url:
            end_user_id = (params or {}).get("end_user_id")
            user = await self._pg.get_user(end_user_id) if end_user_id else None
            if not user:
                return _FakeResponse({"user_id": None})

            payload = {"user_id": end_user_id}
            if isinstance(user, dict):
                payload.update(user)
            return _FakeResponse(payload)

        return _FakeResponse({})

    async def post(self, url: str, json=None, headers=None):
        if "customer/new" in url:
            if self.fail_next_create:
                self.fail_next_create = False
                return _FakeResponse({})

            end_user_id = (json or {}).get("user_id")
            budget_id = (json or {}).get("budget_id")
            if not end_user_id:
                return _FakeResponse({})

            # Minimal LiteLLM end-user record shape needed by `get_or_create_user`.
            self._pg.store_user(
                end_user_id,
                {"user_id": end_user_id, "blocked": False, "budget_id": budget_id},
            )
            return _FakeResponse({})

        return _FakeResponse({})


def test_default_guest_service_types_enforce_shared_guest_cap(
    mocked_client_integration, mocker
):
    from mlpa.core.utils import litellm_pg as admission_pg

    mocker.patch(
        "mlpa.core.utils.get_http_client",
        return_value=_FakeLiteLLMHTTPClient(admission_pg),
    )
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", True)
    mocker.patch.object(env, "MLPA_ENFORCE_GUEST_CAP", True)
    mocker.patch.object(env, "MLPA_MAX_GUESTS", 3)
    mocker.patch.object(env, "MLPA_GUEST_VIRTUAL_KEY", "test-guest-key")
    mocker.patch.object(env, "MLPA_UI_ACCESS_KEY", "test-admin-key")
    tokens = [issue_guest_token() for _ in range(4)]

    def guest_count():
        response = mocked_client_integration.get(
            "/user/signup-cap-status",
            headers={"mlpa_ui_access_key": "Bearer test-admin-key"},
        )
        assert response.status_code == 200
        return response.json()["current_guests"]

    def complete(token, service_type="ai"):
        return mocked_client_integration.post(
            "/v1/chat/completions",
            headers={"authorization": f"Bearer {token}", "service-type": service_type},
            json=SAMPLE_REQUEST.model_dump(),
        )

    for count, token in enumerate(tokens[:3], start=1):
        assert complete(token).status_code == 200
        assert guest_count() == count

    response = complete(tokens[3])
    assert response.status_code == 403
    assert response.json()["detail"]["error"] == ERROR_CODE_MAX_GUESTS_REACHED
    # Reusing a guest or using another feature must not consume another slot.
    assert complete(tokens[0]).status_code == 200
    assert complete(tokens[0], "memories").status_code == 200
    assert guest_count() == 3


def test_managed_cap_rejects_new_identities_and_s2s_bypasses(
    mocked_client_integration, mocker
):
    """Managed service types (ai/memories) are capped; s2s should bypass the cap."""

    user_ids = ["userA", "userB", "userC", "userA"]
    call_idx = {"i": 0}

    def verify_token_side_effect(
        token, scope="profile:uid", include_verification_source=False
    ):
        i = call_idx["i"]
        call_idx["i"] = i + 1
        user = user_ids[i]
        result = {"user": user, "scope": ["profile:uid"]}
        if include_verification_source:
            result["verification_source"] = "local"
        return result

    # Get the mock PG instance that conftest already wired into core admission logic.
    from mlpa.core.utils import litellm_pg as admission_pg

    fake_http_client = _FakeLiteLLMHTTPClient(admission_pg)
    mocker.patch("mlpa.core.utils.get_http_client", return_value=fake_http_client)

    with (
        patch("mlpa.core.config.env.MLPA_MAX_SIGNED_IN_USERS", 1),
        patch("mlpa.core.config.env.MLPA_ENFORCE_SIGNIN_CAP", True),
        patch("mlpa.core.config.env.MLPA_CAPPED_SERVICE_TYPES", ["ai", "memories"]),
    ):
        mocker.patch(
            "mlpa.core.auth.fxa.client.verify_token",
            side_effect=verify_token_side_effect,
        )

        resp1 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
            },
            json=SAMPLE_REQUEST.model_dump(),
        )
        assert resp1.status_code == 200
        assert resp1.json() == SUCCESSFUL_CHAT_RESPONSE

        resp2 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "s2s",
            },
            json=SAMPLE_REQUEST.model_dump(),
        )
        assert resp2.status_code == 200
        assert resp2.json() == SUCCESSFUL_CHAT_RESPONSE

        resp3 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
            },
            json=SAMPLE_REQUEST.model_dump(),
        )
        assert resp3.status_code == 403
        assert resp3.json()["detail"]["error"] == 4

        metrics_resp = mocked_client_integration.get("/metrics")
        assert metrics_resp.status_code == 200
        assert 'reason="signup_cap_exceeded"' in metrics_resp.text

        # Coupling: memories for the same base identity should be allowed.
        resp4 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "memories",
            },
            json=SAMPLE_REQUEST.model_dump(),
        )
        assert resp4.status_code == 200
        assert resp4.json() == SUCCESSFUL_CHAT_RESPONSE


def test_release_reserved_slot_when_litellm_user_creation_fails(
    mocked_client_integration, mocker
):
    """If admission reserves capacity but LiteLLM doesn't create the user row, cap drift must not occur."""

    # Ensure there's room for exactly one managed identity.
    with (
        patch("mlpa.core.config.env.MLPA_MAX_SIGNED_IN_USERS", 1),
        patch("mlpa.core.config.env.MLPA_ENFORCE_SIGNIN_CAP", True),
        patch("mlpa.core.config.env.MLPA_CAPPED_SERVICE_TYPES", ["ai", "memories"]),
    ):
        # Request 1: userA -> admission reserves but user creation fails.
        # Request 2: userB -> should still be admitted because the slot was released.
        user_ids = ["userA", "userB"]
        call_idx = {"i": 0}

        def verify_token_side_effect(
            token, scope="profile:uid", include_verification_source=False
        ):
            i = call_idx["i"]
            call_idx["i"] = i + 1
            user = user_ids[i]
            result = {"user": user, "scope": ["profile:uid"]}
            if include_verification_source:
                result["verification_source"] = "local"
            return result

        from mlpa.core.utils import litellm_pg as admission_pg

        fake_http_client = _FakeLiteLLMHTTPClient(admission_pg)
        fake_http_client.fail_next_create = True
        mocker.patch("mlpa.core.utils.get_http_client", return_value=fake_http_client)

        mocker.patch(
            "mlpa.core.auth.fxa.client.verify_token",
            side_effect=verify_token_side_effect,
        )

        resp1 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
            },
            json=SAMPLE_REQUEST.model_dump(),
        )
        assert resp1.status_code == 500

        resp2 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
            },
            json=SAMPLE_REQUEST.model_dump(),
        )
        assert resp2.status_code == 200
        assert resp2.json() == SUCCESSFUL_CHAT_RESPONSE
