import time

from fastapi import Request

from mlpa.core.logger import logger
from mlpa.core.prometheus_metrics import metrics


async def instrument_requests_middleware(request: Request, call_next):
    """
    Measures request latency, counts total requests, and tracks requests in progress.
    """
    start_time = time.time()
    metrics.in_progress_requests.inc()

    # Forward non-auth headers to log metadata
    with logger.contextualize(
        service_type=request.headers.get("service-type", "N/A"),
        session_id=request.headers.get("session-id", "N/A"),
        user_agent=request.headers.get("user-agent", "N/A"),
        use_app_attest=request.headers.get("use-app-attest", "N/A"),
        request_source=request.headers.get("x-request-source", "N/A"),
    ):
        path = request.url.path
        try:
            # Capture request size if available
            content_length = request.headers.get("content-length")
            if content_length and content_length.isdigit():
                metrics.request_size_bytes.labels(method=request.method).observe(
                    int(content_length)
                )

                logger.info(
                    "Incoming request size captured",
                    extra={
                        "request_method": request.method,
                        "content_length": int(content_length) if content_length else 0,
                        "path": path,
                    },
                )

            response = await call_next(request)

            duration = time.time() - start_time
            route = request.scope.get("route")
            endpoint = route.path if route else request.url.path

            # Capture response size
            res_content_length = response.headers.get("content-length")
            if res_content_length:
                metrics.response_size_bytes.observe(int(res_content_length))
                logger.info(
                    "Response content length captured",
                    extra={
                        "request_method": request.method,
                        "endpoint": endpoint,
                        "path": path,
                        "response_size_bytes": res_content_length,
                        "status_code": response.status_code,
                        "latency_ms": duration,
                    },
                )
            logger.info(
                "Request finished",
                extra={
                    "request_method": request.method,
                    "endpoint": endpoint,
                    "path": path,
                    "response_size_bytes": res_content_length,
                    "status_code": response.status_code,
                    "latency_ms": duration,
                },
            )

            return response
        except Exception as e:
            metrics.request_error_count_total.labels(
                method=request.method, error_type=type(e).__name__
            ).inc()
            logger.error(
                "Request failed with exception",
                extra={
                    "request_method": request.method,
                    "path": request.url.path,
                    "latency_ms": (time.time() - start_time) * 1000,
                    "error_type": type(e).__name__,
                },
                exc_info=True,  # Provides the stack trace for SRE debugging
            )
            raise e
        finally:
            metrics.in_progress_requests.dec()
