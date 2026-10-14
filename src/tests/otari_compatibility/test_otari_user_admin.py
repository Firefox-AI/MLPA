"""
MLPA's user admin routes, backed by Otari's end-user API.
"""

from mlpa.core.config import env
from tests.helpers import (
    CHAT_COMPLETIONS_PATH,
    SERVICE_TYPE,
    chat_request,
    mlpa_headers,
)

ADMIN_HEADERS = {"master_key": f"Bearer {env.MLPA_ADMIN_KEY}"}


class TestUserAdminThroughOtari:
    def test_block_refuses_the_user_until_unblocked(self, real_backend_client):
        """
        Blocking goes to Otari, which enforces it on every request; MLPA no
        longer checks a blocked flag itself, so it must turn Otari's
        `user_blocked` refusal into its own 403 `User is blocked.`, and
        unblocking must let the same user back in.
        """
        client, token, base_identity = real_backend_client
        user_id = f"{base_identity}:{SERVICE_TYPE}"
        headers = mlpa_headers(token)

        # The end user only exists in Otari after its first request.
        response = client.post(
            CHAT_COMPLETIONS_PATH, headers=headers, json=chat_request()
        )
        assert response.status_code == 200, response.text

        response = client.post(f"/user/{user_id}/block", headers=ADMIN_HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["user_id"] == user_id
        assert response.json()["blocked"] is True

        response = client.post(
            CHAT_COMPLETIONS_PATH, headers=headers, json=chat_request()
        )
        assert response.status_code == 403, response.text
        assert response.json()["detail"] == {"error": "User is blocked."}

        response = client.post(f"/user/{user_id}/unblock", headers=ADMIN_HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["blocked"] is False

        response = client.post(
            CHAT_COMPLETIONS_PATH, headers=headers, json=chat_request()
        )
        assert response.status_code == 200, response.text
