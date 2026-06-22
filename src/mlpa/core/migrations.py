from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import mlpa


def _alembic_dir() -> Path:
    # `mlpa` is a namespace package (no __init__.py), so anchor on its path entry:
    # .../src/mlpa -> parents[1] == repo root. The `alembic/` tree lives at the repo
    # root because the image is built `COPY . .` + editable install, not a wheel.
    # Resolved lazily (not at import) so a path problem fails readiness gracefully
    # rather than crashing app startup.
    paths = list(mlpa.__path__)
    if not paths:
        raise RuntimeError("mlpa.__path__ is empty; cannot locate alembic/")
    return Path(paths[0]).resolve().parents[1] / "alembic"


@lru_cache(maxsize=1)
def expected_heads() -> frozenset[str]:
    """Alembic head revision(s) the running code ships, resolved from files.

    Memoized on success only; a failed resolution raises and is not cached, so a
    transient boot hiccup is retried on the next probe.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(_alembic_dir()))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if not heads:
        raise RuntimeError("could not resolve Alembic heads")
    return frozenset(heads)
