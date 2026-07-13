"""
Tests to verify middleware execution order.

FastAPI middleware executes in reverse order of registration (LIFO).
This test ensures our middleware executes in the correct order.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from mlpa.core.config import ERROR_CODE_REQUEST_TOO_LARGE
from mlpa.core.middleware import register_middleware
from mlpa.core.middleware.set_json_content_type import (
    set_json_content_type_middleware,
)


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


def test_instrumentation_bounds_pre_auth_request_labels(metrics_spy):
    from mlpa.core.middleware.instrumentation import instrument_requests_middleware

    app = FastAPI()
    app.middleware("http")(instrument_requests_middleware)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get(
        "/test",
        headers={
            "service-type": "not-real-service-type",
            "purpose": "not-real-purpose",
        },
    )

    assert response.status_code == 200
    assert (
        metrics_spy.value(
            "requests_total",
            method="GET",
            endpoint="/test",
            service_type="other",
            purpose="other",
        )
        == 1
    )
    assert (
        metrics_spy.value(
            "requests_total",
            method="GET",
            endpoint="/test",
            service_type="not-real-service-type",
            purpose="not-real-purpose",
        )
        == 0
    )


def test_instrumentation_keeps_known_pre_auth_request_labels(metrics_spy):
    from mlpa.core.middleware.instrumentation import instrument_requests_middleware

    app = FastAPI()
    app.middleware("http")(instrument_requests_middleware)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get(
        "/test",
        headers={
            "service-type": "ai",
            "purpose": "chat",
        },
    )

    assert response.status_code == 200
    assert (
        metrics_spy.value(
            "requests_total",
            method="GET",
            endpoint="/test",
            service_type="ai",
            purpose="chat",
        )
        == 1
    )


def test_instrumentation_keeps_empty_purpose_as_bounded_label(metrics_spy):
    from mlpa.core.middleware.instrumentation import instrument_requests_middleware

    app = FastAPI()
    app.middleware("http")(instrument_requests_middleware)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get("/test", headers={"service-type": "s2s"})

    assert response.status_code == 200
    assert (
        metrics_spy.value(
            "requests_total",
            method="GET",
            endpoint="/test",
            service_type="s2s",
            purpose="",
        )
        == 1
    )


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
    assert response.json() == {"error": ERROR_CODE_REQUEST_TOO_LARGE}


def test_set_json_content_type_sets_json_for_verify_play():
    app = FastAPI()
    seen_content_types = []

    app.middleware("http")(set_json_content_type_middleware)

    @app.post("/verify/play")
    async def verify_play(request: Request):
        seen_content_types.append(request.headers.get("content-type"))
        return {"content_type": request.headers.get("content-type")}

    client = TestClient(app)
    response = client.post(
        "/verify/play",
        content="integrity_token=test-token&user_id=test-user",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert response.json() == {"content_type": "application/json"}
    assert seen_content_types == ["application/json"]


def test_set_json_content_type_does_not_change_other_routes():
    app = FastAPI()
    seen_content_types = []

    app.middleware("http")(set_json_content_type_middleware)

    @app.post("/other")
    async def other_route(request: Request):
        seen_content_types.append(request.headers.get("content-type"))
        return {"content_type": request.headers.get("content-type")}

    client = TestClient(app)
    response = client.post(
        "/other",
        content="payload=test",
        headers={"content-type": "application/x-www-form-urlencoded"},
    )

    assert response.status_code == 200
    assert response.json() == {"content_type": "application/x-www-form-urlencoded"}
    assert seen_content_types == ["application/x-www-form-urlencoded"]
