from fastapi import Request
from fastapi.responses import JSONResponse

from mlpa.core.config import env
from mlpa.core.logger import logger


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
                    return JSONResponse(
                        status_code=413,
                        content={"error": "Request body too large."},
                    )
            except ValueError:
                pass

    return await call_next(request)
