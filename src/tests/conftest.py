import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mlpa.core.config import env
from tests.metrics_spy import metrics_spy  # noqa: F401 — re-export as fixture


@pytest.fixture(autouse=True)
def _reset_service_type_virtual_keys():
    # Tests patch settings on the shared Env; build its cached map per test.
    env.__dict__.pop("service_type_virtual_keys", None)
    yield
    env.__dict__.pop("service_type_virtual_keys", None)


async def receive_until_cancelled():
    """ASGI receive for a client that stays connected: blocks until cancelled."""
    await asyncio.Event().wait()


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.headers = {}
    # The disconnect watcher awaits receive(). An AsyncMock would return a
    # non-disconnect message and silently route tests through the polling fallback.
    req.receive = receive_until_cancelled
    req.is_disconnected = AsyncMock(return_value=False)
    return req


@pytest.fixture(autouse=True, scope="session")
def _force_mlpa_debug_false():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MLPA_DEBUG", "false")
    monkeypatch.setenv("ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", "false")
    monkeypatch.setenv("ADDITIONAL_FXA_SCOPE_1", "")
    monkeypatch.setenv("ADDITIONAL_FXA_SCOPE_2", "")
    monkeypatch.setenv("ADDITIONAL_FXA_SCOPE_3", "")
    env.MLPA_DEBUG = False
    env.ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT = False
    env.ADDITIONAL_FXA_SCOPE_1 = ""
    env.ADDITIONAL_FXA_SCOPE_2 = ""
    env.ADDITIONAL_FXA_SCOPE_3 = ""
    yield
    monkeypatch.undo()
