"""MLPA access token check used after Play Integrity (HS256 decode + claims)."""

from mlpa.core.utils import extract_user_from_play_integrity_jwt
from tests.bench.fakes import PLAY_USER_ID, play_access_token
from tests.bench.harness import run_sync

OPS = 100


def test_play_integrity_access_token(benchmark):
    header = f"Bearer {play_access_token()}"

    def verify(_):
        assert extract_user_from_play_integrity_jwt(header) == PLAY_USER_ID

    run_sync(benchmark, verify, OPS)
