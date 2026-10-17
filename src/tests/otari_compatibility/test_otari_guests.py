"""
Guests on their own Otari key (OTARI_GUEST_SERVICE_KEY): same owner as the
service key, the guest budgets alone, and its own ceiling.
"""

import jwt

from mlpa.core.config import env
from mlpa.core.routers.guest.guest import issue_guest_token
from tests.helpers import CHAT_COMPLETIONS_PATH, chat_request, mlpa_headers


def test_a_guest_is_served_on_the_guest_budget_and_managed_with_the_service_key(
    real_backend_client,
):
    """
    A guest token turns service type `ai` into `ai-guest`, which MLPA sends on
    the guest key naming the guest budget. Otari only accepts that budget from
    the guest key, so the guest ending up on it shows the key was right; reading
    the guest back through the service key shows both keys share an owner.
    """
    client, _token, _base_identity = real_backend_client
    token = issue_guest_token()
    guest_id = jwt.decode(token, options={"verify_signature": False})["sub"]

    response = client.post(
        CHAT_COMPLETIONS_PATH, headers=mlpa_headers(token), json=chat_request()
    )
    assert response.status_code == 200, response.text

    response = client.get(f"/user/{guest_id}:ai-guest")
    assert response.status_code == 200, response.text
    assert (
        response.json()["budget_id"] == env.service_type_config["ai-guest"]["budget_id"]
    )
