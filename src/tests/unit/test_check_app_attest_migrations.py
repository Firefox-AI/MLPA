from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "check_app_attest_migrations.py"
)
spec = spec_from_file_location("check_app_attest_migrations", SCRIPT)
assert spec is not None and spec.loader is not None
check = module_from_spec(spec)
spec.loader.exec_module(check)

HEAD, OLDER = "c41d7e2a9f10", "b3ea148a6d0b"
KNOWN = {HEAD, OLDER}


@pytest.mark.parametrize(
    ("db_heads", "ready"),
    [({HEAD}, True), ({"f00dfacecafe"}, True), ({OLDER}, False), (set(), False)],
    ids=["current", "newer-release", "behind", "never-migrated"],
)
def test_is_ready(db_heads, ready):
    assert check.is_ready(db_heads, {HEAD}, KNOWN) is ready


def test_waits_until_migrated():
    results = iter([OperationalError("", {}, Exception("down")), {OLDER}, {HEAD}])

    def read_heads():
        result = next(results)
        if isinstance(result, Exception):
            raise result
        return result

    assert check.wait_for_schema(read_heads, {HEAD}, KNOWN, timeout_s=60, interval_s=0)


def test_gives_up_when_still_behind():
    assert not check.wait_for_schema(
        lambda: {OLDER}, {HEAD}, KNOWN, timeout_s=0, interval_s=0
    )
