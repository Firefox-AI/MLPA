import asyncio

from fastapi import HTTPException, Request

from mlpa.core.classes import (
    AuthorizedChatRequest,
    AuthorizedSearchRequest,
)
from mlpa.core.config import env
from mlpa.core.consts import TrafficContractKeyType, TrafficContractMode
from mlpa.core.logger import logger
from mlpa.core.services.services import redis_service


def _set_traffic_contract_modes(
    request: Request | None,
    *,
    rpm_mode: TrafficContractMode | str = "N/A",
    tpm_mode: TrafficContractMode | str = "N/A",
) -> None:
    if request is None:
        return
    request.state.traffic_contract_rpm_mode = rpm_mode
    request.state.traffic_contract_tpm_mode = tpm_mode


async def enforce_traffic_contract(
    request: Request,
    service_type: str,
) -> None:
    _set_traffic_contract_modes(request)

    if not env.ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT:
        return

    contract = env.traffic_contract_config.get(service_type)
    if contract is None:
        return

    try:
        rpm_decision, tpm_decision = await asyncio.gather(
            redis_service.check_feature_traffic_contract(
                key_prefix=env.TRAFFIC_CONTRACT_REDIS_KEY_PREFIX,
                key_type=TrafficContractKeyType.RPM,
                feature=contract["feature"],
                feature_limit=contract["rpm_limit"],
                basket_limit=env.TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT,
                increment_amount=1,
                window_seconds=env.TRAFFIC_CONTRACT_RPM_WINDOW_SECONDS,
            ),
            redis_service.check_feature_traffic_contract(
                key_prefix=env.TRAFFIC_CONTRACT_REDIS_KEY_PREFIX,
                key_type=TrafficContractKeyType.TPM,
                feature=contract["feature"],
                feature_limit=contract["tpm_limit"],
                basket_limit=env.TOTAL_TRAFFIC_CONTRACT_TPM_LIMIT,
                increment_amount=0,  # Token usage is incremented after the response.
                window_seconds=env.TRAFFIC_CONTRACT_TPM_WINDOW_SECONDS,
            ),
        )

        _set_traffic_contract_modes(
            request,
            rpm_mode=rpm_decision.mode,
            tpm_mode=tpm_decision.mode,
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
            "RPM Traffic contract exceeded for "
            f"service_type={service_type}, "
            f"feature={contract['feature']}, limited_by={rpm_decision.limited_by}, "
            f"mode={rpm_decision.mode}, ratio_over={rpm_decision.ratio_over}, "
            f"feature_count={rpm_decision.feature_count}, "
            f"basket_count={rpm_decision.basket_count}"
        )
        # Hard deny over limit requests:
        # raise _rate_limit_response(rpm_decision.retry_after_seconds)

    if not tpm_decision.allowed:
        logger.warning(
            "TPM Traffic contract exceeded for "
            f"service_type={service_type}, "
            f"feature={contract['feature']}, limited_by={tpm_decision.limited_by}, "
            f"mode={tpm_decision.mode}, ratio_over={tpm_decision.ratio_over}, "
            f"feature_count={tpm_decision.feature_count}, "
            f"basket_count={tpm_decision.basket_count}"
        )
        # Hard deny over limit requests:
        # raise _rate_limit_response(tpm_decision.retry_after_seconds)
