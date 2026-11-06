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


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.headers = {}
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


class FakeKVRedis:
    """Minimal get/set Redis stand-in for the guest kill switch."""

    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value


@pytest.fixture(autouse=True)
def fake_redis(monkeypatch):
    # The guest kill switch reads Redis on every guest request. Give each test
    # a clean store and an empty kill switch cache.
    from mlpa.core.services.services import redis_service

    fake = FakeKVRedis()
    monkeypatch.setattr(redis_service, "redis", fake)

    async def _noop():
        pass

    # Lifespan connects when MLPA_GUEST_ENABLED, keep tests off a real Redis
    monkeypatch.setattr(redis_service, "connect", _noop)
    monkeypatch.setattr(redis_service, "close", _noop)
    monkeypatch.setattr(redis_service, "_guests_enabled", True)
    monkeypatch.setattr(redis_service, "_guests_enabled_expires_at", 0.0)
    return fake
