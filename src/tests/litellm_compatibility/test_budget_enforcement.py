"""
Budget enforcement for /v1/chat/completions through MLPA, against real
services.
"""

from mlpa.core.config import env
from tests.helpers import (
    CHAT_COMPLETIONS_PATH,
    chat_request,
    mlpa_headers,
    wait_for_spend,
)

BUDGET_SERVICE_TYPE = "memories"
BUDGET_PURPOSE = "memory-generation"


class TestChatCompletionBudgetEnforcement:
    def test_chat_completion_registers_end_user_with_budget_in_litellm(
        self, real_backend_client, proxy
    ):
        """
        A successful completion must make LiteLLM aware of the end user under
        the memories budget -- i.e. the `user` field actually reached
        LiteLLM. This is the outcome check that a body-shape unit test alone
        (like the one #243 broke) can't provide.
        """
        client, token, base_identity = real_backend_client
        user_id = f"{base_identity}:{BUDGET_SERVICE_TYPE}"

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(
                token, service_type=BUDGET_SERVICE_TYPE, purpose=BUDGET_PURPOSE
            ),
            json=chat_request(),
        )

        assert response.status_code == 200

        info = wait_for_spend(proxy, user_id)
        assert (
            info["litellm_budget_table"]["budget_id"]
            == env.service_type_config[BUDGET_SERVICE_TYPE]["budget_id"]
        )

    def test_chat_completion_enforces_rpm_budget(self, real_backend_client):
        """
        Drives one end user past its configured RPM limit and asserts LiteLLM
        actually rejects it (429, error code 2). This is a behavioral check:
        it doesn't care *how* the budget is enforced, only that it is --
        so it would have caught #243 regardless of which field was dropped.
        """
        client, token, _base_identity = real_backend_client
        rpm_limit = env.service_type_config[BUDGET_SERVICE_TYPE]["rpm_limit"]
        headers = mlpa_headers(
            token, service_type=BUDGET_SERVICE_TYPE, purpose=BUDGET_PURPOSE
        )

        rejected = None
        for _ in range(rpm_limit + 5):
            response = client.post(
                CHAT_COMPLETIONS_PATH, headers=headers, json=chat_request()
            )
            if response.status_code == 429:
                rejected = response
                break
            assert response.status_code == 200, response.text

        assert rejected is not None, (
            f"Sent {rpm_limit + 5} requests for a single user with an "
            f"{rpm_limit}-RPM budget and none were rate-limited -- budget "
            "enforcement is not working."
        )
        assert rejected.json()["detail"]["error"] == 2
        assert rejected.headers.get("Retry-After") == "60"
