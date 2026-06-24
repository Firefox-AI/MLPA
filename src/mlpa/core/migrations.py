from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import mlpa


def _alembic_dir() -> Path:
    # mlpa is a namespace package (no __init__.py), so we can't use __file__.
    # Anchor on the path entry instead: .../src/mlpa, whose parents[1] is the repo
    # root where alembic/ lives (the image is COPY . . + editable install, not a
    # wheel). We do this here rather than at import so a bad path fails the
    # readiness probe instead of crashing startup.
    paths = list(mlpa.__path__)
    if not paths:
        raise RuntimeError("mlpa.__path__ is empty; cannot locate alembic/")
    return Path(paths[0]).resolve().parents[1] / "alembic"


@lru_cache(maxsize=1)
def expected_heads() -> frozenset[str]:
    """Alembic head revision(s) the running code ships, read from the migration files.

    lru_cache only stores successful returns, so a failed resolution raises and
    gets retried on the next probe rather than being cached.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(_alembic_dir()))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if not heads:
        raise RuntimeError("could not resolve Alembic heads")
    return frozenset(heads)
