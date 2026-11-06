from unittest.mock import AsyncMock

import pytest

from mlpa import run
from mlpa.core.config import env
from tests.mocks import MockAppAttestPGService, MockLiteLLMPGService


@pytest.mark.parametrize("guests, contracts", [(True, False), (False, True)])
async def test_redis_down_fails_startup(mocker, guests, contracts):
    litellm_pg = MockLiteLLMPGService()
    mocker.patch.object(run, "litellm_pg", litellm_pg)
    mocker.patch.object(run, "app_attest_pg", MockAppAttestPGService(litellm_pg))
    mocker.patch.object(
        run.redis_service, "connect", AsyncMock(side_effect=ConnectionError("down"))
    )
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", guests)
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", contracts)
    with pytest.raises(ConnectionError):
        async with run.lifespan(run.app):
            pass
