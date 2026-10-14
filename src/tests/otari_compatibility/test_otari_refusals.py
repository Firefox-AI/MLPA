"""
Otari's refusals, mapped from its Otari-Error-Code header onto the error
codes MLPA's clients handle.
"""

from mlpa.core.config import env
from tests.helpers import (
    CHAT_COMPLETIONS_PATH,
    OTARI_TINY_BUDGET_SERVICE_TYPE,
    chat_request,
    mlpa_headers,
)


class TestOtariRefusals:
    def test_per_user_budget_refusal_is_error_1(self, real_backend_client):
        """
        Otari refuses an end user over its own budget with `budget_exceeded`
        and `Otari-Budget-Scope: user`, which MLPA must answer as its
        per-user budget error (429, error 1) -- not the global one (error
        10), which the same code means under any other scope.
        """
        client, token, _ = real_backend_client

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(
                token,
                service_type=OTARI_TINY_BUDGET_SERVICE_TYPE,
                # A dev service type, so it takes the experimentation token too.
                **{"x-dev-authorization": env.MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN},
            ),
            json=chat_request(),
        )

        assert response.status_code == 429, response.text
        assert response.json()["detail"]["error"] == 1
        # The seconds until the daily budget resets at UTC midnight.
        assert 1 <= int(response.headers["Retry-After"]) <= 86400

    def test_unknown_model_is_error_8(self, real_backend_client):
        """Otari refuses a model it can't route with `invalid_model`, which
        MLPA must answer as 400, error 8, rather than a 500."""
        client, token, _ = real_backend_client

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(model="no-such-model"),
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"]["error"] == 8
