import pytest

from mlpa.core.config import env


@pytest.fixture(autouse=True, scope="session")
def _force_mlpa_debug_false():
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("MLPA_DEBUG", "false")
    env.MLPA_DEBUG = False
    yield
    monkeypatch.undo()
