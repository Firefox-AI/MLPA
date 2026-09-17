"""
Budget provisioning in LiteLLM's Postgres and Redis.

MLPA doesn't ask LiteLLM's API to create budgets -- it writes them straight
into LiteLLM's own schema with hand-written SQL (litellm_pg_service.
create_budget, update_user_budget). That makes the table and column names
an MLPA-facing contract, and a bump that alters them breaks MLPA with no
MLPA code changing.

Worse, create_budget catches every exception and only logs it. A schema
change therefore leaves MLPA starting up cleanly with no budgets at all,
and the only visible symptom is unlimited spend. Nothing else in the repo
would notice, which is why these read the database directly.

The Redis check covers the other half: per-user limits are only enforced
across replicas while LiteLLM keeps its counters in Redis rather than in
process memory.
"""

import pytest

from mlpa.core.config import env
from tests.helpers import (
    BUDGET_TABLE,
    CHAT_COMPLETIONS_PATH,
    END_USER_TABLE,
    SERVICE_TYPE,
    chat_request,
    mlpa_headers,
)


class TestBudgetProvisioningInPostgres:
    @pytest.mark.parametrize("service_type", sorted(env.user_feature_budget))
    async def test_configured_budget_is_written_to_litellms_schema(
        self, litellm_db, service_type
    ):
        """Every service type MLPA configures must have a matching row.
        Parametrised so a single missing or wrong budget names itself
        instead of hiding behind an aggregate assertion."""
        expected = env.user_feature_budget[service_type]

        row = await litellm_db.fetchrow(
            f"SELECT max_budget, rpm_limit, tpm_limit, budget_duration "
            f'FROM "{BUDGET_TABLE}" WHERE budget_id = $1',
            expected["budget_id"],
        )

        assert row is not None, (
            f"No {BUDGET_TABLE} row for budget_id={expected['budget_id']!r} "
            f"(service_type={service_type}). MLPA's create_budget swallows "
            "SQL errors, so this is what a schema change looks like."
        )
        assert row["max_budget"] == expected["max_budget"]
        assert row["rpm_limit"] == expected["rpm_limit"]
        assert row["tpm_limit"] == expected["tpm_limit"]
        assert row["budget_duration"] == expected["budget_duration"]

    async def test_completion_links_the_end_user_to_its_budget(
        self, real_backend_client, litellm_db
    ):
        """update_user_budget writes budget_id onto the end-user row with
        its own raw UPDATE, against a different table -- so it can break
        independently of budget creation."""
        client, token, base_identity = real_backend_client
        user_id = f"{base_identity}:{SERVICE_TYPE}"

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(),
        )
        assert response.status_code == 200, response.text

        budget_id = await litellm_db.fetchval(
            f'SELECT budget_id FROM "{END_USER_TABLE}" WHERE user_id = $1',
            user_id,
        )

        assert budget_id == env.user_feature_budget[SERVICE_TYPE]["budget_id"], (
            f"End user {user_id!r} is not linked to its budget -- MLPA's "
            f"UPDATE against {END_USER_TABLE} did not take effect."
        )


class TestUserManagementAdminEndpoints:
    """litellm_pg_service.py's block_user/update_user_budget are also
    reachable through MLPA's own /user/{id}/block and /user/{id}/budget
    admin endpoints (master_key auth), separate from the
    get_or_create_user path test_completion_links_the_end_user_to_its_
    budget covers above."""

    async def test_block_and_budget_update_persist_to_real_table(
        self, real_backend_client, litellm_db
    ):
        client, token, base_identity = real_backend_client
        user_id = f"{base_identity}:{SERVICE_TYPE}"

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(),
        )
        assert response.status_code == 200, response.text

        block_response = client.post(
            f"/user/{user_id}/block",
            headers={"master_key": f"Bearer {env.MASTER_KEY}"},
        )
        assert block_response.status_code == 200, block_response.text

        new_budget_id = env.user_feature_budget["memories-dev"]["budget_id"]
        budget_response = client.post(
            f"/user/{user_id}/budget",
            headers={"master_key": f"Bearer {env.MASTER_KEY}"},
            json={"service_type": "memories-dev"},
        )
        assert budget_response.status_code == 200, budget_response.text
        assert budget_response.json()["budget_id"] == new_budget_id

        row = await litellm_db.fetchrow(
            f'SELECT blocked, budget_id FROM "{END_USER_TABLE}" WHERE user_id = $1',
            user_id,
        )
        assert row is not None, f"{user_id!r} not found in {END_USER_TABLE}"
        assert row["blocked"] is True
        assert row["budget_id"] == new_budget_id


class TestRateLimitStateInRedis:
    async def test_per_user_limits_are_tracked_in_redis(
        self, real_backend_client, redis_client
    ):
        """Asserts only that some key mentions this end user, not the exact
        key format -- the format is LiteLLM's business, but whether the
        counters are shared at all is MLPA's."""
        client, token, base_identity = real_backend_client
        user_id = f"{base_identity}:{SERVICE_TYPE}"

        response = client.post(
            CHAT_COMPLETIONS_PATH,
            headers=mlpa_headers(token),
            json=chat_request(),
        )
        assert response.status_code == 200, response.text

        matching = [key for key in redis_client.scan_iter(count=500) if user_id in key]

        assert matching, (
            f"No Redis key references end user {user_id!r} after a "
            "completion. LiteLLM is tracking per-user limits in process "
            "memory, so budgets won't be enforced across replicas."
        )
