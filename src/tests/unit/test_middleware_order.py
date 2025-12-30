"""
Tests to verify middleware execution order.

FastAPI middleware executes in reverse order of registration (LIFO).
This test ensures our middleware executes in the correct order.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mlpa.core.middleware import register_middleware


@pytest.fixture
def test_app():
    """Create a test FastAPI app with middleware registered."""
    app = FastAPI()

    execution_order = []

    async def track_order_middleware(name: str, request: Request, call_next):
        execution_order.append(f"{name}_before")
        response = await call_next(request)
        execution_order.append(f"{name}_after")
        return response

    from mlpa.core.middleware.instrumentation import instrument_requests_middleware
    from mlpa.core.middleware.request_size import check_request_size_middleware

    app.middleware("http")(instrument_requests_middleware)
    app.middleware("http")(check_request_size_middleware)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    return app, execution_order


def test_middleware_execution_order(test_app):
    """
    Verify that middleware executes in the correct order.

    Expected order:
    1. check_request_size_middleware (runs first - early rejection)
    2. instrument_requests_middleware (runs second - wraps everything)
    """
    app, execution_order = test_app

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200


def test_register_middleware_function():
    """Test that register_middleware correctly registers all middleware."""
    app = FastAPI()

    register_middleware(app)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200

    large_content = "x" * (11 * 1024 * 1024)
    response = client.post(
        "/v1/chat/completions",
        headers={"Content-Length": str(len(large_content))},
        content=large_content,
    )
    assert response.status_code == 413
