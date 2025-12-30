from fastapi import Request

from mlpa.core.config import env


async def security_headers_middleware(request: Request, call_next):
    """
    Security headers to all API responses.

    Sets X-Content-Type-Options to prevent MIME type sniffing.
    Sets Strict-Transport-Security (HSTS) when HTTPS is detected via X-Forwarded-Proto.
    """
    response = await call_next(request)

    if not env.SECURITY_HEADERS_ENABLED:
        return response

    response.headers["X-Content-Type-Options"] = "nosniff"

    forwarded_proto = request.headers.get("X-Forwarded-Proto", "")
    is_https = request.url.scheme == "https" or forwarded_proto == "https"

    if is_https:
        hsts_value = f"max-age={env.HSTS_MAX_AGE}"
        if env.HSTS_INCLUDE_SUBDOMAINS:
            hsts_value += "; includeSubDomains"
        response.headers["Strict-Transport-Security"] = hsts_value

    return response
