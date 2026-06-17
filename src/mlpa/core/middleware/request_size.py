from fastapi import Request
from fastapi.responses import JSONResponse

from mlpa.core.config import ERROR_CODE_REQUEST_TOO_LARGE, env
from mlpa.core.logger import logger
from mlpa.core.metrics import record_chat_availability_for
from mlpa.core.prometheus_metrics import AvailabilityReason


async def check_request_size_middleware(request: Request, call_next):
    """
    Checks request body size for /v1/chat/completions endpoint to prevent oversized requests.
    Validates Content-Length header before processing the request body.
    """
    if request.url.path == "/v1/chat/completions" and request.method == "POST":
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                size = int(content_length)
                if size > env.MAX_REQUEST_SIZE_BYTES:
                    logger.warning(
                        f"Request size {size} bytes exceeds maximum {env.MAX_REQUEST_SIZE_BYTES} bytes"
                    )
                    # `model` is in the request body, which we don't read here.
                    # We reject on the Content-Length header without parsing it.
                    record_chat_availability_for(
                        AvailabilityReason.PAYLOAD_TOO_LARGE,
                        model="",
                        service_type=(
                            request.headers.get("service-type") or ""
                        ).strip(),
                        purpose=(request.headers.get("purpose") or "").strip(),
                    )
                    return JSONResponse(
                        status_code=413,
                        content={"error": ERROR_CODE_REQUEST_TOO_LARGE},
                    )
            except ValueError:
                pass

    return await call_next(request)
