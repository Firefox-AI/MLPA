from unittest.mock import AsyncMock, MagicMock

import pytest

from mlpa.core.config import env
from tests.metrics_spy import metrics_spy  # noqa: F401 — re-export as fixture


@pytest.fixture
def mock_request():
    req = MagicMock()
    req.is_disconnected = AsyncMock(return_value=False)
    return req


@pytest.fixture(autouse=True, scope="session")
def _force_mlpa_debug_false():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MLPA_DEBUG", "false")
    monkeypatch.setenv("ADDITIONAL_FXA_SCOPE_1", None)
    monkeypatch.setenv("ADDITIONAL_FXA_SCOPE_2", None)
    monkeypatch.setenv("ADDITIONAL_FXA_SCOPE_3", None)
    env.MLPA_DEBUG = False
    env.ADDITIONAL_FXA_SCOPE_1 = None
    env.ADDITIONAL_FXA_SCOPE_2 = None
    env.ADDITIONAL_FXA_SCOPE_3 = None
    yield
    monkeypatch.undo()
