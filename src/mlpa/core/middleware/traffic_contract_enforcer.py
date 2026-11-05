from fastapi import HTTPException, Request

from mlpa.core.classes import AuthorizedChatRequest, AuthorizedSearchRequest
from mlpa.core.config import ERROR_CODE_TRAFFIC_CONTRACT_EXCEEDED, env
from mlpa.core.consts import TrafficContractMode
from mlpa.core.logger import logger
from mlpa.core.metrics import record_availability
from mlpa.core.prometheus_metrics import AvailabilityReason
from mlpa.core.request_timings import measure
from mlpa.core.services.services import redis_service


def _rate_limit_response(retry_after_seconds: int) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail={"error": ERROR_CODE_TRAFFIC_CONTRACT_EXCEEDED},
        headers={"Retry-After": str(retry_after_seconds)},
    )


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
    authorized_request: AuthorizedSearchRequest | AuthorizedChatRequest,
) -> None:
    with measure("traffic_contract", request):
        _set_traffic_contract_modes(request)

        if not env.ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT:
            return
        service_type = authorized_request.service_type
        contract = env.traffic_contract_config.get(service_type)
        if contract is None:
            return

        try:
            (
                rpm_decision,
                tpm_decision,
            ) = await redis_service.check_feature_traffic_contracts(
                key_prefix=env.TRAFFIC_CONTRACT_REDIS_KEY_PREFIX,
                feature=contract["feature"],
                rpm_limit=contract["rpm_limit"],
                tpm_limit=contract["tpm_limit"],
                rpm_basket_limit=env.TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT,
                tpm_basket_limit=env.TOTAL_TRAFFIC_CONTRACT_TPM_LIMIT,
                rpm_increment_amount=1,
                tpm_increment_amount=0,  # Token usage is incremented after the response.
                rpm_window_seconds=env.TRAFFIC_CONTRACT_RPM_WINDOW_SECONDS,
                tpm_window_seconds=env.TRAFFIC_CONTRACT_TPM_WINDOW_SECONDS,
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

        _set_traffic_contract_modes(
            request, rpm_mode=rpm_decision.mode, tpm_mode=tpm_decision.mode
        )
        for dimension, decision in (("RPM", rpm_decision), ("TPM", tpm_decision)):
            if decision.allowed:
                continue
            logger.warning(
                f"{dimension} Traffic contract exceeded for "
                f"service_type={service_type}, "
                f"feature={contract['feature']}, limited_by={decision.limited_by}, "
                f"mode={decision.mode}, ratio_over={decision.ratio_over}, "
                f"feature_count={decision.feature_count}, "
                f"basket_count={decision.basket_count}"
            )
            if env.TRAFFIC_CONTRACT_429_IF_NOT_ALLOWED:
                record_availability(
                    authorized_request, AvailabilityReason.TRAFFIC_CONTRACT_VIOLATION
                )
                raise _rate_limit_response(decision.retry_after_seconds)
