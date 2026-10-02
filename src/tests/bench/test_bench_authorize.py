"""Dispatch overhead of `authorize_chat_request` for each auth header combination.

The auth backends are replaced with instant stubs, so this measures only the
routing work: header checks, purpose and model validation, App Attest JWT
parsing and body hashing, and building the AuthorizedChatRequest.
"""

import pytest
from fastapi import Request

from mlpa.core.auth import authorize as authorize_module
from mlpa.core.classes import ChatRequest, ServiceType
from mlpa.core.config import env
from tests.bench.fakes import (
    APP_ATTEST_BUNDLE_ID,
    DEV_AUTH_TOKEN,
    FXA_STUB_TOKEN,
    FXA_USER_ID,
    PLAY_USER_ID,
    AppAttestDevice,
    play_access_token,
)
from tests.bench.harness import run_async

OPS = 20
BODY = b'{"model":"mock","messages":[{"role":"user","content":"hello"}]}'
CHAT_REQUEST = ChatRequest(
    model="mock", messages=[{"role": "user", "content": "hello"}]
)


async def _fxa_auth(authorization):
    return {"user": FXA_USER_ID}


async def _auth_with_key(x_dev_authorization, authorization):
    return {"user": FXA_USER_ID}


async def _app_attest_auth(assertion_auth, expected_hash, use_qa_certificates):
    return {"status": "success"}


def _extract_user(authorization):
    return PLAY_USER_ID


def _app_attest_header():
    device = AppAttestDevice(
        "bench-dispatch", f"{env.APP_DEVELOPMENT_TEAM}.{APP_ATTEST_BUNDLE_ID}"
    )
    return f"Bearer {device.assertion_jwt('bench-challenge', b'unused')}"


COMBINATIONS = {
    "fxa": lambda: {
        "authorization": f"Bearer {FXA_STUB_TOKEN}",
        "service_type": ServiceType("ai"),
    },
    "dev": lambda: {
        "authorization": f"Bearer {FXA_STUB_TOKEN}",
        "service_type": ServiceType("ai-dev"),
        "x_dev_authorization": DEV_AUTH_TOKEN,
    },
    "play_integrity": lambda: {
        "authorization": f"Bearer {play_access_token()}",
        "service_type": ServiceType("ai"),
        "use_play_integrity": True,
    },
    "app_attest": lambda: {
        "authorization": _app_attest_header(),
        "service_type": ServiceType("ai"),
        "use_app_attest": True,
    },
}


def _make_request() -> Request:
    async def receive() -> dict:
        return {"type": "http.request", "body": BODY, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/chat/completions",
            "headers": [],
        },
        receive,
    )


@pytest.mark.parametrize("combination", COMBINATIONS)
def test_authorize_chat_request_dispatch(benchmark, loop, monkeypatch, combination):
    monkeypatch.setattr(authorize_module, "fxa_auth", _fxa_auth)
    monkeypatch.setattr(authorize_module, "auth_with_key", _auth_with_key)
    monkeypatch.setattr(authorize_module, "app_attest_auth", _app_attest_auth)
    monkeypatch.setattr(
        authorize_module, "extract_user_from_play_integrity_jwt", _extract_user
    )
    headers = COMBINATIONS[combination]()

    async def authorize(_):
        result = await authorize_module.authorize_chat_request(
            request=_make_request(),
            chat_request=CHAT_REQUEST,
            purpose="chat",
            **headers,
        )
        assert result.user.endswith(headers["service_type"].value)

    run_async(benchmark, loop, authorize, OPS)
