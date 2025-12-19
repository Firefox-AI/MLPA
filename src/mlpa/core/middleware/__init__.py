"""
Application middleware module.

HOW MIDDLEWARE ORDER WORKS:
===========================

FastAPI executes middleware in REVERSE order of registration (LIFO - Last In First Out).

Example: If you register A, then B, then C:
  Registration order: A -> B -> C
  Execution order:    C -> B -> A -> handler -> A -> B -> C

To ensure correct execution order:
1. Define desired execution order in MIDDLEWARE_EXECUTION_ORDER
2. Register them in REVERSE order using register_middleware()

CURRENT EXECUTION ORDER (request -> response):
1. check_request_size_middleware - Early rejection of oversized requests
2. instrument_requests_middleware - Wraps everything for metrics/logging
3. [Request Handler]
4. instrument_requests_middleware - Records metrics
5. check_request_size_middleware - Returns response
"""

from mlpa.core.middleware.instrumentation import instrument_requests_middleware
from mlpa.core.middleware.request_size import check_request_size_middleware

__all__ = [
    "check_request_size_middleware",
    "instrument_requests_middleware",
    "register_middleware",
    "MIDDLEWARE_EXECUTION_ORDER",
]

# Define middleware execution order explicitly (innermost to outermost)
# This is the DESIRED execution order from request to handler
# register_middleware() will reverse this for registration
MIDDLEWARE_EXECUTION_ORDER = [
    check_request_size_middleware,
    instrument_requests_middleware,
]


def register_middleware(app):
    """
    Register all application middleware in the correct execution order.

    This function ensures middleware executes in the order defined by
    MIDDLEWARE_EXECUTION_ORDER, handling FastAPI's LIFO registration.

    Args:
        app: FastAPI application instance

    Execution Flow:
        Request -> check_request_size -> instrument_requests -> handler
        -> instrument_requests -> check_request_size -> Response

    To add new middleware:
        1. Create the middleware function
        2. Add it to MIDDLEWARE_EXECUTION_ORDER in the desired position
        3. Import it at the top of this file
        4. Add to __all__ if needed
    """
    # FastAPI executes middleware in reverse registration order (LIFO)
    # So we reverse MIDDLEWARE_EXECUTION_ORDER to get the correct execution order
    for middleware_func in reversed(MIDDLEWARE_EXECUTION_ORDER):
        app.middleware("http")(middleware_func)
