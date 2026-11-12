"""
has_managed_user_rows builds exact user_ids and matches them on the primary
key, so it relies on LiteLLM storing them as "{base_identity}:{service_type}".
This runs it against the real schema.
"""

import uuid

from mlpa.core.config import env
from mlpa.core.services.litellm_pg_service import LiteLLMPGService
from tests.helpers import SERVICE_TYPE


async def test_has_managed_user_rows_against_real_table(proxy):
    base_identity = f"e2e-cap-{uuid.uuid4().hex[:12]}"
    response = proxy.post(
        "/customer/new",
        json={
            "user_id": f"{base_identity}:{SERVICE_TYPE}",
            "budget_id": env.service_type_config[SERVICE_TYPE]["budget_id"],
        },
    )
    assert response.status_code == 200, response.text

    service = LiteLLMPGService()
    await service.connect()
    try:
        assert await service.has_managed_user_rows(base_identity, [SERVICE_TYPE])
        assert not await service.has_managed_user_rows(base_identity, ["memories"])
    finally:
        await service.disconnect()
