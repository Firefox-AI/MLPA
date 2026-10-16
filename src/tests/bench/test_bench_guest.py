from mlpa.core.config import env
from tests.bench.harness import run_sync

OPS = 100


def test_guest_token_verify(benchmark, monkeypatch):
    # Imported here: bench_compare.py runs this file on main too, where
    # verify_guest_token may not exist yet. A module import would fail the
    # whole base run at collection, this only fails this bench.
    from mlpa.core.routers.guest.guest import issue_guest_token, verify_guest_token

    monkeypatch.setattr(env, "MLPA_GUEST_ENABLED", True)
    header = f"Bearer {issue_guest_token()}"

    def verify(_):
        assert verify_guest_token(header)

    run_sync(benchmark, verify, OPS)
