import pytest
from fastapi import HTTPException

from mlpa.core.config import env
from mlpa.core.middleware import traffic_contract_enforcer
from mlpa.core.services.redis_service import TrafficContractDecision
from tests.consts import SAMPLE_REQUEST


async def test_chat_traffic_contract_noops_when_disabled(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", False)
    check = mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_and_increment_feature_rpm",
        mocker.AsyncMock(),
    )

    await traffic_contract_enforcer.enforce_chat_traffic_contract(SAMPLE_REQUEST)

    check.assert_not_awaited()


async def test_chat_traffic_contract_increments_feature_rpm_when_enabled(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    check = mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_and_increment_feature_rpm",
        mocker.AsyncMock(
            return_value=TrafficContractDecision(
                allowed=True,
                feature_count=1,
                basket_count=1,
                retry_after_seconds=60,
            )
        ),
    )

    await traffic_contract_enforcer.enforce_chat_traffic_contract(SAMPLE_REQUEST)

    contract = env.traffic_contract_config[SAMPLE_REQUEST.service_type]
    check.assert_awaited_once_with(
        key_prefix=env.TRAFFIC_CONTRACT_REDIS_KEY_PREFIX,
        feature=contract["feature"],
        feature_rpm_limit=contract["rpm_limit"],
        basket_rpm_limit=contract["basket_rpm_limit"],
        window_seconds=env.TRAFFIC_CONTRACT_RPM_WINDOW_SECONDS,
        ttl_seconds=env.TRAFFIC_CONTRACT_COUNTER_TTL_SECONDS,
    )


async def test_chat_traffic_contract_allows_soft_over_rpm(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    check = mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_and_increment_feature_rpm",
        mocker.AsyncMock(
            return_value=TrafficContractDecision(
                allowed=False,
                feature_count=10_000,
                basket_count=10_000,
                retry_after_seconds=17,
                limited_by="feature",
                mode="borrowed",
                ratio_over=1.1,
            )
        ),
    )

    await traffic_contract_enforcer.enforce_chat_traffic_contract(SAMPLE_REQUEST)

    check.assert_awaited_once()


async def test_chat_traffic_contract_can_fail_open_on_redis_error(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    mocker.patch.object(env, "TRAFFIC_CONTRACT_FAIL_OPEN_ON_REDIS_ERROR", True)
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_and_increment_feature_rpm",
        mocker.AsyncMock(side_effect=RuntimeError("redis down")),
    )

    await traffic_contract_enforcer.enforce_chat_traffic_contract(SAMPLE_REQUEST)


async def test_chat_traffic_contract_fails_closed_on_redis_error(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    mocker.patch.object(env, "TRAFFIC_CONTRACT_FAIL_OPEN_ON_REDIS_ERROR", False)
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_and_increment_feature_rpm",
        mocker.AsyncMock(side_effect=RuntimeError("redis down")),
    )

    with pytest.raises(HTTPException) as exc_info:
        await traffic_contract_enforcer.enforce_chat_traffic_contract(SAMPLE_REQUEST)

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error": "Traffic contract enforcement unavailable."
    }
