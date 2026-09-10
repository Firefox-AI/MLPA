from fastapi import HTTPException

from mlpa.core.classes import AuthorizedChatRequest, AuthorizedSearchRequest
from mlpa.core.config import TrafficContractConfig, env
from mlpa.core.logger import logger
from mlpa.core.metrics import record_chat_availability
from mlpa.core.prometheus_metrics import AvailabilityReason
from mlpa.core.services.services import redis_service


def _traffic_contract_for_service_type(
    service_type: str,
) -> TrafficContractConfig | None:
    return env.traffic_contract_config.get(service_type)


async def _enforce_traffic_contract(service_type: str) -> None:
    if not env.ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT:
        return

    contract = _traffic_contract_for_service_type(service_type)
    if contract is None:
        return
    logger.info(contract)

    try:
        rpm_decision = await redis_service.check_and_increment_feature_rpm(
            key_prefix=env.TRAFFIC_CONTRACT_REDIS_KEY_PREFIX,
            feature=contract["feature"],
            feature_rpm_limit=contract["rpm_limit"],
            window_seconds=env.TRAFFIC_CONTRACT_RPM_WINDOW_SECONDS,
            ttl_seconds=env.TRAFFIC_CONTRACT_COUNTER_TTL_SECONDS,
        )
    except Exception as exc:
        logger.error(
            "Traffic contract enforcement failed for "
            f"service_type={service_type}: {exc}"
        )
        if env.TRAFFIC_CONTRACT_FAIL_OPEN_ON_REDIS_ERROR:
            return
        raise HTTPException(
            status_code=503,
            detail={"error": "Traffic contract enforcement unavailable."},
        ) from exc

    if not rpm_decision.allowed:
        logger.warning(
            "Traffic contract exceeded for "
            f"service_type={service_type}, "
            f"feature={contract['feature']}, limited_by={rpm_decision.limited_by}, "
            f"mode={rpm_decision.mode}, ratio_over={rpm_decision.ratio_over}, "
            f"feature_count={rpm_decision.feature_count}, "
            f"basket_count={rpm_decision.basket_count}"
        )
        # Hard deny over limit requests:
        # raise _rate_limit_response(rpm_decision.retry_after_seconds)


async def enforce_chat_traffic_contract(req: AuthorizedChatRequest) -> None:
    try:
        await _enforce_traffic_contract(req.service_type)
    except HTTPException as exc:
        if exc.status_code >= 500:
            record_chat_availability(req, AvailabilityReason.PROVISIONING_FAILURE)
        raise


async def enforce_search_traffic_contract(
    req: AuthorizedSearchRequest,
) -> None:
    try:
        await _enforce_traffic_contract(req.service_type)
    except HTTPException:
        raise
