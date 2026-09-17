"""
Shared helpers for MLPA test suite
"""

import asyncio
import json
import time

import asyncpg
import httpx

from mlpa.core.config import env

CHAT_COMPLETIONS_PATH = "/v1/chat/completions"

MOCK_MODEL = "mock"
MOCK_RESPONSE_TEXT = "this is a mocked response"

# Default service type, and purpose used for validating completions
SERVICE_TYPE = "ai"
PURPOSE = "chat"

BUDGET_TABLE = "LiteLLM_BudgetTable"
END_USER_TABLE = "LiteLLM_EndUserTable"


def _db_reachable(db_name: str) -> bool:
    async def _connect() -> bool:
        try:
            conn = await asyncpg.connect(
                f"{env.PG_DB_URL.rstrip('/')}/{db_name}", timeout=2.0
            )
            await conn.close()
            return True
        except Exception:
            return False

    return asyncio.run(_connect())


def real_backend_available() -> bool:
    try:
        httpx.get(
            f"{env.LITELLM_API_BASE}/health/liveliness", timeout=2.0
        ).raise_for_status()
    except Exception:
        return False
    return _db_reachable(env.LITELLM_DB_NAME) and _db_reachable(env.APP_ATTEST_DB_NAME)


class FxAStub:
    """Maps a single bearer token to a single FxA base identity, so each
    test can get a fresh, collision-free user_id against the real LiteLLM
    end-user table."""

    def __init__(self, token: str, base_identity: str):
        self._token = token
        self._base_identity = base_identity

    def verify_token(
        self, token: str, scope: str = "profile:uid", include_verification_source=False
    ):
        if token == self._token:
            result = {"user": self._base_identity}
            if include_verification_source:
                result["verification_source"] = "local"
            return result
        raise Exception("Invalid token")


def chat_request(**overrides) -> dict:
    body = {
        "model": MOCK_MODEL,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    body.update(overrides)
    return body


def mlpa_headers(
    token: str,
    service_type: str = SERVICE_TYPE,
    purpose: str = PURPOSE,
    **overrides,
) -> dict:
    """Client-facing headers for MLPA's own endpoint. Tests that target a
    specific budget pass their own service_type/purpose."""
    headers = {
        "authorization": f"Bearer {token}",
        "service-type": service_type,
        "purpose": purpose,
    }
    headers.update(overrides)
    return headers


def sse_content(body: str) -> str:
    """Reassemble the assistant message from an SSE stream."""
    chunks = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        for choice in data.get("choices", []):
            content = choice.get("delta", {}).get("content")
            if content:
                chunks.append(content)
    return "".join(chunks)


def _poll(predicate, *, timeout_s: float, interval_s: float = 1.0):
    deadline = time.monotonic() + timeout_s
    result = predicate()
    while not result and time.monotonic() < deadline:
        time.sleep(interval_s)
        result = predicate()
    return result


def key_spend(proxy: httpx.Client, key: str) -> float:
    response = proxy.get("/key/info", params={"key": key})
    response.raise_for_status()
    return response.json()["info"]["spend"]


def customer_info(proxy: httpx.Client, user_id: str) -> dict | None:
    """Ask LiteLLM itself whether it knows about this end user."""
    response = proxy.get("/customer/info", params={"end_user_id": user_id})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


def wait_for_spend(
    proxy: httpx.Client, user_id: str, *, above: float = 0.0, timeout_s: float = 60.0
) -> dict:
    """LiteLLM writes spend logs via a batched background worker (~15s in
    local docker-compose), so poll for it instead of asserting immediately
    after the request returns."""
    info = _poll(
        lambda: (lambda i: i if i is not None and i["spend"] > above else None)(
            customer_info(proxy, user_id)
        ),
        timeout_s=timeout_s,
    )
    assert info is not None, (
        f"LiteLLM recorded no spend above {above} for end user {user_id!r} "
        f"within {timeout_s}s of a successful completion -- the `user` field "
        "likely isn't reaching LiteLLM."
    )
    return info


def wait_for_key_spend(
    proxy: httpx.Client, key: str, *, above: float = 0.0, timeout_s: float = 60.0
) -> float:
    """Poll until the key's recorded spend exceeds `above`."""
    spend = _poll(
        lambda: (lambda s: s if s > above else None)(key_spend(proxy, key)),
        timeout_s=timeout_s,
    )
    assert spend is not None, (
        f"LiteLLM recorded no spend above {above} against the virtual key "
        f"within {timeout_s}s of a successful completion -- key-level spend "
        "accounting is not working."
    )
    return spend
