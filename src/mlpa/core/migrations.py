from functools import lru_cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import mlpa

# `mlpa` is a namespace package (no __init__.py), so anchor on its path entry:
# .../src/mlpa -> parents[1] == repo root. The `alembic/` tree lives at the repo
# root because the image is built `COPY . .` + editable install, not a wheel.
_ALEMBIC_DIR = Path(next(iter(mlpa.__path__))).resolve().parents[1] / "alembic"


@lru_cache(maxsize=1)
def expected_heads() -> frozenset[str]:
    """Alembic head revision(s) the running code ships, resolved from files.

    Memoized on success only; a failed resolution raises and is not cached, so a
    transient boot hiccup is retried on the next probe.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(_ALEMBIC_DIR))
    heads = ScriptDirectory.from_config(cfg).get_heads()
    if not heads:
        raise RuntimeError("could not resolve Alembic heads")
    return frozenset(heads)
