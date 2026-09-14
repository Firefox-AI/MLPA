"""
LiteLLM upgrade contract test (AIPLAT-910).

The LiteLLM image tag is pinned (`litellm_docker_compose.yaml`); bumps have
caused incidents before with no pre-prod test to catch them: a spend-query
design issue, and a v1.83.10 regression where end-user budgets stopped
resetting. This file boots the real image (same compose stack as the rest of
src/tests/integration) and asserts behaviors that broke before.

This is the first, minimal slice: just Prisma migrations applying cleanly.
Auth accept/reject, full request-shape coverage, budget-reset, and MLPA's
direct-SQL writes into LiteLLM's own tables are coming in follow-up PRs.

Requires `make docker-up` and a migrated app_attest DB, same as the rest of
this directory. Skips locally if the real backend is unreachable; CI sets
MLPA_TEST_REQUIRE_REAL_BACKEND=true so the same situation fails the build
instead.
"""

import asyncio
import os

import asyncpg
import httpx
import pytest

from mlpa.core.config import env


def _db_reachable(db_name: str) -> bool:
    async def _connect() -> bool:
        try:
            conn = await asyncpg.connect(
                f"{env.PG_DB_URL.rstrip('/')}/{db_name}", timeout=2.0
            )
            await conn.close()
            return True
        except Exception:
            return False

    return asyncio.run(_connect())


def _real_backend_available() -> bool:
    try:
        httpx.get(
            f"{env.LITELLM_API_BASE}/health/liveliness", timeout=2.0
        ).raise_for_status()
    except Exception:
        return False
    return _db_reachable(env.LITELLM_DB_NAME) and _db_reachable(env.APP_ATTEST_DB_NAME)


_BACKEND_AVAILABLE = _real_backend_available()
_REQUIRE_REAL_BACKEND = os.environ.get(
    "MLPA_TEST_REQUIRE_REAL_BACKEND", ""
).lower() in {
    "1",
    "true",
    "yes",
}

if not _BACKEND_AVAILABLE and _REQUIRE_REAL_BACKEND:
    pytest.fail(
        "MLPA_TEST_REQUIRE_REAL_BACKEND is set but LiteLLM/Postgres are "
        "unreachable -- CI's docker-compose setup is broken.",
        pytrace=False,
    )

pytestmark = pytest.mark.skipif(
    not _BACKEND_AVAILABLE,
    reason=(
        "Requires a live LiteLLM proxy + Postgres. Run `make docker-up` "
        "and `uv run alembic upgrade head` first."
    ),
)


async def _litellm_conn() -> asyncpg.Connection:
    return await asyncpg.connect(
        f"{env.PG_DB_URL.rstrip('/')}/{env.LITELLM_DB_NAME}", timeout=5.0
    )


class TestPrismaMigrations:
    def test_migrations_applied_cleanly(self):
        """LiteLLM's Prisma migrations ran to completion on boot. A
        migration that fails partway can still leave the proxy answering
        /health/liveliness, so check the migration ledger directly rather
        than inferring health from that endpoint."""

        async def _fetch():
            conn = await _litellm_conn()
            try:
                return await conn.fetch(
                    "SELECT migration_name, finished_at, rolled_back_at "
                    'FROM "_prisma_migrations"'
                )
            finally:
                await conn.close()

        rows = asyncio.run(_fetch())
        assert rows, "no rows in _prisma_migrations -- LiteLLM's schema never migrated"

        broken = [
            row["migration_name"]
            for row in rows
            if row["finished_at"] is None or row["rolled_back_at"] is not None
        ]
        assert not broken, f"migrations left in a bad state: {broken}"
