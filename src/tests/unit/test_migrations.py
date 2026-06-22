from pathlib import Path
from unittest.mock import MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

import mlpa
from mlpa.core import migrations


@pytest.fixture(autouse=True)
def clear_heads_cache():
    migrations.expected_heads.cache_clear()
    yield
    migrations.expected_heads.cache_clear()


def _script_directory():
    alembic_dir = Path(next(iter(mlpa.__path__))).resolve().parents[1] / "alembic"
    cfg = Config()
    cfg.set_main_option("script_location", str(alembic_dir))
    return ScriptDirectory.from_config(cfg)


def test_expected_heads_match_script_directory():
    expected = set(_script_directory().get_heads())
    assert migrations.expected_heads() == frozenset(expected)


def test_expected_heads_support_multiple_heads(monkeypatch):
    fake = MagicMock()
    fake.get_heads.return_value = ["aaa111", "bbb222"]
    monkeypatch.setattr(migrations.ScriptDirectory, "from_config", lambda cfg: fake)
    assert migrations.expected_heads() == frozenset({"aaa111", "bbb222"})


def test_expected_heads_memoized(monkeypatch):
    fake = MagicMock()
    fake.get_heads.return_value = ["aaa111"]
    from_config = MagicMock(return_value=fake)
    monkeypatch.setattr(migrations.ScriptDirectory, "from_config", from_config)

    migrations.expected_heads()
    migrations.expected_heads()

    from_config.assert_called_once()


def test_expected_heads_raise_when_unresolvable(monkeypatch):
    fake = MagicMock()
    fake.get_heads.return_value = []
    monkeypatch.setattr(migrations.ScriptDirectory, "from_config", lambda cfg: fake)
    with pytest.raises(RuntimeError):
        migrations.expected_heads()
