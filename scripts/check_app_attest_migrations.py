"""Init container check: wait until the app_attest schema has been migrated.

It never migrates; the mlpa-migrations Job does. It stops new pods starting
when that Job was skipped (Argo CD skips hooks on a partial sync). A schema
newer than this image passes, so rollbacks and canaries aren't blocked.

Exits 0 once ready, 1 after MIGRATION_CHECK_TIMEOUT_S (default 300).
"""

import os
import sys
import time
from collections.abc import Callable
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, create_engine, pool
from sqlalchemy.exc import SQLAlchemyError

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


def log(message: str) -> None:
    print(f"[mlpa-appattest-check] {message}", flush=True)


def is_ready(db_heads: set[str], script_heads: set[str], known: set[str]) -> bool:
    # A revision this image doesn't know comes from a newer release.
    return db_heads == script_heads or bool(db_heads - known)


def wait_for_schema(
    read_heads: Callable[[], set[str]],
    script_heads: set[str],
    known: set[str],
    timeout_s: float,
    interval_s: float,
) -> bool:
    started = time.monotonic()
    while True:
        try:
            db_heads = read_heads()
            if is_ready(db_heads, script_heads, known):
                log(
                    f"ready at {sorted(db_heads)} after {time.monotonic() - started:.1f}s"
                )
                return True
            log(f"schema {sorted(db_heads)} is behind {sorted(script_heads)}, waiting")
        except SQLAlchemyError as exc:
            log(f"database not reachable: {str(exc).splitlines()[0]}")

        if time.monotonic() - started >= timeout_s:
            log(f"gave up after {timeout_s:.0f}s; did the mlpa-migrations Job run?")
            return False
        time.sleep(interval_s)


def main() -> int:
    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    known = {rev.revision for rev in script.walk_revisions()}
    engine = create_engine(
        URL.create(
            "postgresql+psycopg2",
            username=os.environ["DB_USERNAME"],
            password=os.environ["DB_PASSWORD"],
            host=os.environ["DB_HOST"],
            port=int(os.environ["DB_PORT"]),
            database=os.environ["APP_ATTEST_DB_NAME"],
        ),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 5},
    )

    def read_heads() -> set[str]:
        with engine.connect() as connection:
            return set(MigrationContext.configure(connection).get_current_heads())

    ready = wait_for_schema(
        read_heads,
        set(script.get_heads()),
        known,
        timeout_s=float(os.environ.get("MIGRATION_CHECK_TIMEOUT_S", "300")),
        interval_s=float(os.environ.get("MIGRATION_CHECK_INTERVAL_S", "5")),
    )
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
