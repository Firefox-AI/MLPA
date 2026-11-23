"""
Tests to verify middleware execution order.

FastAPI middleware executes in reverse order of registration (LIFO).
This test ensures our middleware executes in the correct order.
"""

from typing import Annotated

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from mlpa.core.auth import authorize as authorize_module
from mlpa.core.classes import AuthorizedChatRequest
from mlpa.core.config import ERROR_CODE_REQUEST_TOO_LARGE, env
from mlpa.core.middleware import register_middleware
from mlpa.core.middleware.set_json_content_type import (
    set_json_content_type_middleware,
)
from mlpa.core.routers.guest.guest import issue_guest_token


def test_instrumentation_bounds_pre_auth_request_labels(metrics_spy, mocker):
    from mlpa.core.middleware import instrumentation

    log_context = mocker.spy(instrumentation.logger, "contextualize")
    app = FastAPI()
    app.middleware("http")(instrumentation.instrument_requests_middleware)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)
    response = client.get(
        "/test",
        headers={
            "service-type": "not-real-service-type",
            "purpose": "not-real-purpose",
            "use-app-attest": "true",
        },
    )

    assert response.status_code == 200
    assert "auth_type" not in log_context.call_args.kwargs
    assert "use_app_attest" not in log_context.call_args.kwargs
    assert (
        metrics_spy.value(
            "requests_total",
            method="GET",
            endpoint="/test",
            service_type="other",
            auth_type="N/A",
            purpose="other",
            major_fx_version="",
            traffic_contract_rpm_mode="N/A",
            traffic_contract_tpm_mode="N/A",
        )
        == 1
    )
    assert (
        metrics_spy.value(
            "requests_total",
            method="GET",
            endpoint="/test",
            service_type="not-real-service-type",
            auth_type="N/A",
            purpose="not-real-purpose",
            major_fx_version="",
            traffic_contract_rpm_mode="N/A",
            traffic_contract_tpm_mode="N/A",
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
            auth_type="N/A",
            purpose="chat",
            major_fx_version="",
            traffic_contract_rpm_mode="N/A",
            traffic_contract_tpm_mode="N/A",
        )
        == 1
    )


def test_instrumentation_uses_traffic_contract_mode_from_request_state(metrics_spy):
    from mlpa.core.middleware.instrumentation import instrument_requests_middleware

    app = FastAPI()
    app.middleware("http")(instrument_requests_middleware)

    @app.get("/test")
    async def test_endpoint(request: Request):
        request.state.traffic_contract_rpm_mode = "borrowed"
        request.state.traffic_contract_tpm_mode = "borrowed"
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
            auth_type="N/A",
            purpose="chat",
            major_fx_version="",
            traffic_contract_rpm_mode="borrowed",
            traffic_contract_tpm_mode="borrowed",
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
            auth_type="N/A",
            purpose="",
            major_fx_version="",
            traffic_contract_rpm_mode="N/A",
            traffic_contract_tpm_mode="N/A",
        )
        == 1
    )


def test_instrumentation_buckets_unknown_methods(metrics_spy):
    from mlpa.core.middleware.instrumentation import instrument_requests_middleware

    app = FastAPI()
    app.middleware("http")(instrument_requests_middleware)

    @app.get("/test")
    async def test_endpoint():
        return {"status": "ok"}

    client = TestClient(app)
    response = client.request(
        "BREW",
        "/test",
        headers={
            "service-type": "ai",
            "purpose": "chat",
        },
    )

    assert response.status_code in {404, 405}
    assert (
        metrics_spy.value(
            "requests_total",
            method="INVALID",
            endpoint="/test",
            service_type="ai",
            auth_type="N/A",
            purpose="chat",
            major_fx_version="",
            traffic_contract_rpm_mode="N/A",
            traffic_contract_tpm_mode="N/A",
        )
        == 1
    )
    assert (
        metrics_spy.value(
            "requests_total",
            method="BREW",
            endpoint="/test",
            service_type="ai",
            auth_type="N/A",
            purpose="chat",
            major_fx_version="",
            traffic_contract_rpm_mode="N/A",
            traffic_contract_tpm_mode="N/A",
        )
        == 0
    )


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


def test_instrumentation_tracks_valid_firefox_major_version(metrics_spy):
    from mlpa.core.middleware.instrumentation import instrument_requests_middleware
    from mlpa.core.utils import parse_firefox_major_version_from_user_agent

    user_agent = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:155.0) Gecko/20100101 Firefox/155.0"

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
            "user-agent": user_agent,
        },
    )

    assert response.status_code == 200
    assert (
        metrics_spy.value(
            "requests_total",
            method="GET",
            endpoint="/test",
            service_type="ai",
            auth_type="N/A",
            purpose="chat",
            major_fx_version=parse_firefox_major_version_from_user_agent(user_agent),
            traffic_contract_rpm_mode="N/A",
            traffic_contract_tpm_mode="N/A",
        )
        == 1
    )


@pytest.mark.parametrize(
    ("service_type", "auth_headers", "status_code", "auth_type"),
    [
        ("ai", {}, 200, "FxA"),
        ("ai", {"use-app-attest": "false", "use-play-integrity": "off"}, 200, "FxA"),
        ("ai", {"use-app-attest": "invalid"}, 422, "N/A"),
        ("ai", {"use-app-attest": "true"}, 401, "AppAttest"),
        (
            "s2s",
            {"use-app-attest": "true", "use-play-integrity": "true"},
            200,
            "AppAttest",
        ),
        (
            "s2s-android",
            {"use-app-attest": "0", "use-play-integrity": "true"},
            200,
            "PlayIntegrity",
        ),
        ("ai-guest", {}, 200, "Guest"),
    ],
)
def test_instrumentation_records_selected_auth_type(
    metrics_spy, mocker, monkeypatch, service_type, auth_headers, status_code, auth_type
):
    from mlpa.core.middleware.instrumentation import instrument_requests_middleware

    mocker.patch.object(authorize_module, "fxa_auth", return_value={"user": "fxa-user"})
    parser = mocker.patch.object(authorize_module, "parse_app_attest_jwt")
    parser.return_value.key_id_b64 = "app-attest-user"
    mocker.patch.object(
        authorize_module,
        "app_attest_auth",
        return_value={"error": "Invalid assertion"}
        if status_code == 401
        else {"status": "success"},
    )
    mocker.patch.object(
        authorize_module,
        "extract_user_from_play_integrity_jwt",
        return_value="play-integrity-user",
    )

    authorization = "Bearer test-token"
    if service_type == "ai-guest":
        monkeypatch.setattr(env, "MLPA_GUEST_ENABLED", True)
        monkeypatch.setattr(env, "MLPA_GUEST_VIRTUAL_KEY", "sk-test-guest")
        monkeypatch.setattr(env, "MLPA_GUEST_TOKEN_SIGNING_SECRET", "a" * 32)
        authorization = f"Bearer {issue_guest_token()}"

    app = FastAPI()
    app.middleware("http")(instrument_requests_middleware)

    @app.post("/chat")
    async def chat(
        authorized: Annotated[
            AuthorizedChatRequest, Depends(authorize_module.authorize_chat_request)
        ],
    ):
        return authorized.log_fields

    client_service_type = service_type.removesuffix("-guest")
    headers = {
        "authorization": authorization,
        "service-type": client_service_type,
        **auth_headers,
    }
    purpose = "chat" if client_service_type == "ai" else ""
    if purpose:
        headers["purpose"] = purpose

    response = TestClient(app).post(
        "/chat", headers=headers, json={"model": "mock", "messages": []}
    )

    assert response.status_code == status_code
    if status_code == 200:
        assert response.json()["auth_type"] == auth_type
    assert (
        metrics_spy.value(
            "requests_total",
            method="POST",
            endpoint="/chat",
            service_type=service_type,
            auth_type=auth_type,
            purpose=purpose,
            major_fx_version="",
            traffic_contract_rpm_mode="N/A",
            traffic_contract_tpm_mode="N/A",
        )
        == 1
    )
