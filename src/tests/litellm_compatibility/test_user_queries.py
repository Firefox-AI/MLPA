"""
MLPA's hand-written reads against LiteLLM_EndUserTable (AIPLAT-1510).

The unit tests mock the pool, so only this suite runs the primary key lookup
in has_managed_user_rows and the cached get_user against the schema
LiteLLM's Prisma migrations actually create. Users are
provisioned through LiteLLM's own /customer/new, the same way
get_or_create_user does it.
"""

import uuid

import httpx
import pytest

from mlpa.core.config import env
from mlpa.core.services.litellm_pg_service import LiteLLMPGService
from tests.helpers import SERVICE_TYPE


@pytest.fixture
async def litellm_pg():
    service = LiteLLMPGService()
    await service.connect()
    try:
        yield service
    finally:
        await service.disconnect()


def _create_end_users(proxy: httpx.Client, user_ids: list[str]) -> None:
    budget_id = env.service_type_config[SERVICE_TYPE]["budget_id"]
    for user_id in user_ids:
        response = proxy.post(
            "/customer/new", json={"user_id": user_id, "budget_id": budget_id}
        )
        assert response.status_code == 200, response.text


class TestManagedUserRows:
    async def test_primary_key_lookup_finds_only_managed_rows(self, proxy, litellm_pg):
        base_identity = f"e2e-cap-{uuid.uuid4().hex[:12]}"
        _create_end_users(proxy, [f"{base_identity}:{SERVICE_TYPE}"])

        assert await litellm_pg.has_managed_user_rows(
            base_identity, [SERVICE_TYPE, "memories"]
        )
        assert not await litellm_pg.has_managed_user_rows(base_identity, ["memories"])
        assert not await litellm_pg.has_managed_user_rows(
            f"{base_identity}-other", [SERVICE_TYPE]
        )


class TestGetUserCache:
    async def test_block_is_visible_on_the_same_pod_immediately(
        self, proxy, litellm_pg
    ):
        user_id = f"e2e-cache-{uuid.uuid4().hex[:12]}:{SERVICE_TYPE}"
        _create_end_users(proxy, [user_id])

        assert (await litellm_pg.get_user(user_id))["blocked"] is False
        await litellm_pg.block_user(user_id, blocked=True)

        assert (await litellm_pg.get_user(user_id))["blocked"] is True
