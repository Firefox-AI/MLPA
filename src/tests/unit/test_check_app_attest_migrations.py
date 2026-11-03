"""AIPLAT-1577: the MLPA init container that blocks pod startup until the
mlpa-migrations Job has brought the app_attest schema up to this image's head.
"""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.exc import OperationalError

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_check_script():
    path = REPO_ROOT / "scripts" / "check_app_attest_migrations.py"
    spec = spec_from_file_location("check_app_attest_migrations", path)
    assert spec is not None
    assert spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = _load_check_script()

HEAD = "c41d7e2a9f10"
PARENT = "b3ea148a6d0b"
KNOWN = {HEAD, PARENT, "919c4d382c42"}


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _heads_sequence(*results):
    calls = iter(results)

    def read_heads() -> set[str]:
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    return read_heads


@pytest.mark.parametrize(
    ("db_heads", "expected"),
    [
        ({HEAD}, check.SchemaStatus.CURRENT),
        ({"f00dfacecafe"}, check.SchemaStatus.AHEAD),
        ({PARENT}, check.SchemaStatus.BEHIND),
        (set(), check.SchemaStatus.BEHIND),
    ],
    ids=["at-head", "newer-release", "older-revision", "never-migrated"],
)
def test_classify(db_heads, expected):
    assert check.classify(db_heads, {HEAD}, KNOWN) is expected


def test_returns_immediately_when_schema_is_current():
    clock = FakeClock()

    ready = check.wait_for_schema(
        _heads_sequence({HEAD}),
        {HEAD},
        KNOWN,
        timeout_s=60,
        interval_s=5,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert ready is True
    assert clock.sleeps == []


def test_lets_old_pods_start_against_a_newer_schema():
    # Rollback / canary: an older image scaling up after a newer release migrated.
    clock = FakeClock()

    ready = check.wait_for_schema(
        _heads_sequence({"f00dfacecafe"}),
        {HEAD},
        KNOWN,
        timeout_s=60,
        interval_s=5,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert ready is True


def test_waits_through_behind_and_unreachable_until_job_catches_up():
    clock = FakeClock()
    unreachable = OperationalError("SELECT 1", {}, Exception("connection refused"))

    ready = check.wait_for_schema(
        _heads_sequence({PARENT}, unreachable, {HEAD}),
        {HEAD},
        KNOWN,
        timeout_s=60,
        interval_s=5,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert ready is True
    assert len(clock.sleeps) == 2


def test_gives_up_when_schema_stays_behind(capsys):
    clock = FakeClock()

    def always_behind() -> set[str]:
        return {PARENT}

    ready = check.wait_for_schema(
        always_behind,
        {HEAD},
        KNOWN,
        timeout_s=20,
        interval_s=5,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )

    assert ready is False
    assert clock.now == pytest.approx(20)
    assert "gave up" in capsys.readouterr().out


def test_script_revisions_match_repo_migrations():
    script = ScriptDirectory.from_config(Config(str(check.ALEMBIC_INI)))

    heads, known = check.load_script_revisions(script)

    assert len(heads) == 1, "app_attest migrations should have a single head"
    assert heads <= known
    assert len(known) == len(list((REPO_ROOT / "alembic" / "versions").glob("*.py")))
