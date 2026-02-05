import time

from fastapi import Request

from mlpa.core.logger import logger
from mlpa.core.prometheus_metrics import metrics


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
            service_type = request.headers.get("service-type", "NA")

            metrics.request_latency.labels(
                method=request.method, endpoint=endpoint
            ).observe(time.perf_counter() - start_time)
            metrics.requests_total.labels(
                method=request.method, endpoint=endpoint, service_type=service_type
            ).inc()
            metrics.response_status_codes.labels(status_code=response.status_code).inc()
            return response
        finally:
            metrics.in_progress_requests.dec()
