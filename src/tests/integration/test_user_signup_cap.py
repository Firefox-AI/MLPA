from unittest.mock import patch

import pytest

from mlpa.core.config import env
from tests.consts import SUCCESSFUL_CHAT_RESPONSE, TEST_FXA_TOKEN


@pytest.fixture
def use_real_get_or_create_user():
    # Exercise the real get_or_create_user() implementation instead of the mocked one.
    return True


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
        result = {"user": user}
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
            "mlpa.core.routers.fxa.fxa.client.verify_token",
            side_effect=verify_token_side_effect,
        )

        resp1 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
        assert resp1.status_code == 200
        assert resp1.json() == SUCCESSFUL_CHAT_RESPONSE

        resp2 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "s2s",
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
        assert resp2.status_code == 200
        assert resp2.json() == SUCCESSFUL_CHAT_RESPONSE

        resp3 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
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
            json={"messages": [{"role": "user", "content": "Hello"}]},
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
            result = {"user": user}
            if include_verification_source:
                result["verification_source"] = "local"
            return result

        from mlpa.core.utils import litellm_pg as admission_pg

        fake_http_client = _FakeLiteLLMHTTPClient(admission_pg)
        fake_http_client.fail_next_create = True
        mocker.patch("mlpa.core.utils.get_http_client", return_value=fake_http_client)

        mocker.patch(
            "mlpa.core.routers.fxa.fxa.client.verify_token",
            side_effect=verify_token_side_effect,
        )

        resp1 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
        assert resp1.status_code == 500

        resp2 = mocked_client_integration.post(
            "/v1/chat/completions",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "ai",
            },
            json={"messages": [{"role": "user", "content": "Hello"}]},
        )
        assert resp2.status_code == 200
        assert resp2.json() == SUCCESSFUL_CHAT_RESPONSE
