import time

from fastapi import Request

from mlpa.core.consts import TrafficContractKeyType, TrafficContractMode
from mlpa.core.logger import logger
from mlpa.core.prometheus_metrics import metrics
from mlpa.core.utils import (
    clamp_major_fx_version,
    clamp_purpose,
    clamp_request_method,
    clamp_service_type,
    parse_firefox_major_version_from_user_agent,
)


def _traffic_contract_mode_label(
    request: Request,
    key_type: TrafficContractKeyType,
) -> str:
    mode = getattr(
        request.state,
        f"traffic_contract_{key_type.value}_mode",
        "N/A",
    )
    if isinstance(mode, TrafficContractMode):
        return mode.value
    if isinstance(mode, str) and mode in {mode.value for mode in TrafficContractMode}:
        return mode
    return "N/A"


async def instrument_requests_middleware(request: Request, call_next):
    """
    Measures request latency, counts total requests, and tracks requests in progress.
    """
    start_time = time.perf_counter()
    metrics.in_progress_requests.inc()

    # Forward non-auth headers to log metadata
    with logger.contextualize(
        service_type=request.headers.get("service-type", "N/A"),
        session_id=request.headers.get("session-id", "N/A"),
        user_agent=request.headers.get("user-agent", "N/A"),
        use_app_attest=request.headers.get("use-app-attest", "N/A"),
    ):
        try:
            response = await call_next(request)

            route = request.scope.get("route")
            endpoint = route.path if route else request.url.path

            method = clamp_request_method(request.method)
            service_type = request.headers.get("service-type", "")
            purpose = request.headers.get("purpose", "")
            user_agent = request.headers.get("user-agent", "")
            major_fx_version = parse_firefox_major_version_from_user_agent(user_agent)
            metrics.request_latency.labels(method=method, endpoint=endpoint).observe(
                time.perf_counter() - start_time
            )
            metrics.requests_total.labels(
                method=method,
                endpoint=endpoint,
                service_type=clamp_service_type(service_type),
                purpose=clamp_purpose(purpose),
                major_fx_version=clamp_major_fx_version(major_fx_version),
                traffic_contract_rpm_mode=_traffic_contract_mode_label(
                    request, TrafficContractKeyType.RPM
                ),
                traffic_contract_tpm_mode=_traffic_contract_mode_label(
                    request, TrafficContractKeyType.TPM
                ),
            ).inc()
            metrics.response_status_codes.labels(status_code=response.status_code).inc()
            return response
        finally:
            metrics.in_progress_requests.dec()
