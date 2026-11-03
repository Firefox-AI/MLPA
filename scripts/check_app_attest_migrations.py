"""Wait until the app_attest schema is at least as new as this image's migrations.

Runs as an init container on the MLPA Deployment (AIPLAT-1577). It never runs
migrations itself: that stays with the mlpa-migrations Job. Its only job is to
keep a new pod from starting when the Job didn't run, e.g. when an Argo CD
partial sync of just the Deployment skipped the Sync hook. The pod then sits in
Init until the Job catches the schema up, and the rollout stalls instead of
serving against a schema that's missing tables or columns.

The database counts as ready when it's at this image's head revision(s), or
when it's on a revision this image doesn't know about. The second case is a
newer release's schema (a canary, or old pods scaling up after a code rollback),
and blocking it would turn every rollback into an outage.

Exit codes: 0 when the schema is ready, 1 when the timeout runs out first.

Env: DB_HOST, DB_PORT, DB_USERNAME, DB_PASSWORD, APP_ATTEST_DB_NAME.
Optional: MIGRATION_CHECK_TIMEOUT_S (default 300), MIGRATION_CHECK_INTERVAL_S
(default 5).
"""

import os
import random
import sys
import time
from collections.abc import Callable
from enum import Enum
from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Engine, create_engine, pool
from sqlalchemy.exc import SQLAlchemyError

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"
LOG_PREFIX = "[mlpa-appattest-check]"


class SchemaStatus(Enum):
    CURRENT = "current"
    AHEAD = "ahead"
    BEHIND = "behind"


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", flush=True)


def classify(
    db_heads: set[str], script_heads: set[str], known_revisions: set[str]
) -> SchemaStatus:
    if db_heads == script_heads:
        return SchemaStatus.CURRENT
    if db_heads - known_revisions:
        return SchemaStatus.AHEAD
    return SchemaStatus.BEHIND


def load_script_revisions(script: ScriptDirectory) -> tuple[set[str], set[str]]:
    heads = set(script.get_heads())
    known = {rev.revision for rev in script.walk_revisions()}
    return heads, known


def database_url() -> URL:
    return URL.create(
        "postgresql+psycopg2",
        username=os.environ["DB_USERNAME"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=int(os.environ["DB_PORT"]),
        database=os.environ["APP_ATTEST_DB_NAME"],
    )


def read_db_heads(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(MigrationContext.configure(connection).get_current_heads())


def wait_for_schema(
    read_heads: Callable[[], set[str]],
    script_heads: set[str],
    known_revisions: set[str],
    timeout_s: float,
    interval_s: float,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> bool:
    started = monotonic()
    attempt = 0
    while True:
        attempt += 1
        try:
            db_heads = read_heads()
        except SQLAlchemyError as exc:
            reason = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0]
            log(f"attempt {attempt}: database not reachable yet: {reason}")
        else:
            status = classify(db_heads, script_heads, known_revisions)
            elapsed = monotonic() - started
            if status is SchemaStatus.CURRENT:
                log(
                    f"schema is current at {sorted(db_heads)} after {elapsed:.1f}s ({attempt} attempt(s))"
                )
                return True
            if status is SchemaStatus.AHEAD:
                log(
                    f"schema {sorted(db_heads)} is newer than this image's head "
                    f"{sorted(script_heads)}; continuing after {elapsed:.1f}s"
                )
                return True
            log(
                f"attempt {attempt}: schema {sorted(db_heads) or 'empty'} is behind "
                f"this image's head {sorted(script_heads)}; waiting for the "
                "mlpa-migrations Job"
            )

        elapsed = monotonic() - started
        if elapsed >= timeout_s:
            log(
                f"gave up after {elapsed:.1f}s: schema never reached "
                f"{sorted(script_heads)}. Run the mlpa-migrations Job (a full "
                "Argo CD sync runs it as a Sync hook)."
            )
            return False
        # Jitter so a scale-up's worth of pods don't poll in lockstep.
        sleep(min(interval_s * random.uniform(0.8, 1.2), max(timeout_s - elapsed, 0)))


def main() -> int:
    timeout_s = float(os.environ.get("MIGRATION_CHECK_TIMEOUT_S", "300"))
    interval_s = float(os.environ.get("MIGRATION_CHECK_INTERVAL_S", "5"))

    script = ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))
    script_heads, known_revisions = load_script_revisions(script)
    log(
        f"waiting up to {timeout_s:.0f}s for {os.environ['APP_ATTEST_DB_NAME']} "
        f"on {os.environ['DB_HOST']}:{os.environ['DB_PORT']} to reach {sorted(script_heads)}"
    )

    engine = create_engine(
        database_url(),
        poolclass=pool.NullPool,
        connect_args={"connect_timeout": 5},
    )
    try:
        ready = wait_for_schema(
            lambda: read_db_heads(engine),
            script_heads,
            known_revisions,
            timeout_s,
            interval_s,
        )
    finally:
        engine.dispose()
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
