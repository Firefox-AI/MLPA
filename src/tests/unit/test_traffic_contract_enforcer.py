from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from mlpa.core.classes import TrafficContractDecision
from mlpa.core.config import env
from mlpa.core.consts import TrafficContractMode
from mlpa.core.middleware import traffic_contract_enforcer
from tests.consts import SAMPLE_REQUEST


def _request_with_state():
    return SimpleNamespace(state=SimpleNamespace(), headers={})


def _decision(
    *,
    allowed: bool = True,
    limited_by: str | None = None,
    mode: TrafficContractMode = TrafficContractMode.NORMAL,
    ratio_over: float | None = None,
) -> TrafficContractDecision:
    return TrafficContractDecision(
        allowed=allowed,
        feature_count=1,
        basket_count=1,
        retry_after_seconds=60,
        limited_by=limited_by,
        mode=mode,
        ratio_over=ratio_over,
    )


async def test_traffic_contract_noops_when_disabled(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", False)
    check = mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_feature_traffic_contracts",
        mocker.AsyncMock(),
    )
    increment = mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )
    request = _request_with_state()

    await traffic_contract_enforcer.enforce_traffic_contract(
        request, SAMPLE_REQUEST.service_type
    )

    assert request.state.traffic_contract_rpm_mode == "N/A"
    check.assert_not_awaited()
    increment.assert_not_awaited()


async def test_traffic_contract_checks_feature_rpm_and_tpm_when_enabled(
    mocker,
):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    check = mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_feature_traffic_contracts",
        mocker.AsyncMock(return_value=(_decision(), _decision())),
    )
    update_contracts = mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )
    request = _request_with_state()

    await traffic_contract_enforcer.enforce_traffic_contract(
        request, SAMPLE_REQUEST.service_type
    )

    contract = env.traffic_contract_config[SAMPLE_REQUEST.service_type]
    check.assert_awaited_once_with(
        key_prefix=env.TRAFFIC_CONTRACT_REDIS_KEY_PREFIX,
        feature=contract["feature"],
        rpm_limit=contract["rpm_limit"],
        tpm_limit=contract["tpm_limit"],
        rpm_basket_limit=env.TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT,
        tpm_basket_limit=env.TOTAL_TRAFFIC_CONTRACT_TPM_LIMIT,
        rpm_increment_amount=1,
        tpm_increment_amount=0,
        rpm_window_seconds=env.TRAFFIC_CONTRACT_RPM_WINDOW_SECONDS,
        tpm_window_seconds=env.TRAFFIC_CONTRACT_TPM_WINDOW_SECONDS,
    )
    update_contracts.assert_not_awaited()
    assert request.state.traffic_contract_rpm_mode == "normal"


async def test_traffic_contract_records_borrowed_mode_when_feature_is_over_contract(
    mocker,
):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_feature_traffic_contracts",
        mocker.AsyncMock(
            return_value=(
                _decision(
                    allowed=False,
                    limited_by="feature",
                    mode=TrafficContractMode.BORROWED,
                    ratio_over=1.1,
                ),
                _decision(),
            )
        ),
    )
    update_contracts = mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )
    request = _request_with_state()

    await traffic_contract_enforcer.enforce_traffic_contract(
        request, SAMPLE_REQUEST.service_type
    )

    update_contracts.assert_not_awaited()
    assert request.state.traffic_contract_rpm_mode == "borrowed"


async def test_traffic_contract_records_rpm_and_tpm_modes(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_feature_traffic_contracts",
        mocker.AsyncMock(
            return_value=(
                _decision(
                    allowed=False,
                    limited_by="feature",
                    mode=TrafficContractMode.BORROWED,
                    ratio_over=1.1,
                ),
                _decision(
                    allowed=False,
                    limited_by="basket",
                    mode=TrafficContractMode.DEGRADED,
                    ratio_over=1.2,
                ),
            )
        ),
    )
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )
    request = _request_with_state()

    await traffic_contract_enforcer.enforce_traffic_contract(
        request, SAMPLE_REQUEST.service_type
    )

    assert request.state.traffic_contract_rpm_mode == "borrowed"
    assert request.state.traffic_contract_tpm_mode == "degraded"


async def test_traffic_contract_can_fail_open_on_redis_error(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    mocker.patch.object(env, "TRAFFIC_CONTRACT_FAIL_OPEN_ON_REDIS_ERROR", True)
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_feature_traffic_contracts",
        mocker.AsyncMock(side_effect=RuntimeError("redis down")),
    )
    increment = mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )
    request = _request_with_state()

    await traffic_contract_enforcer.enforce_traffic_contract(
        request, SAMPLE_REQUEST.service_type
    )

    assert request.state.traffic_contract_rpm_mode == "N/A"
    increment.assert_not_awaited()


async def test_traffic_contract_fails_closed_on_redis_error(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    mocker.patch.object(env, "TRAFFIC_CONTRACT_FAIL_OPEN_ON_REDIS_ERROR", False)
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_feature_traffic_contracts",
        mocker.AsyncMock(side_effect=RuntimeError("redis down")),
    )

    request = _request_with_state()
    with pytest.raises(HTTPException) as exc_info:
        await traffic_contract_enforcer.enforce_traffic_contract(
            request, SAMPLE_REQUEST.service_type
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == {
        "error": "Traffic contract enforcement unavailable."
    }
