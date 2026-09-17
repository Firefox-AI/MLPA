"""
Prisma migration completeness for the pinned LiteLLM image.
"""

from tests.helpers import list_litellm_container_dir

MIGRATIONS_DIR = "/app/litellm-proxy-extras/litellm_proxy_extras/migrations"


class TestPrismaMigrations:
    async def test_migrations_applied_cleanly(self, litellm_db):
        """A migration that fails partway can still leave the proxy
        answering /health/liveliness, so check the migration ledger
        directly rather than inferring health from that endpoint."""
        rows = await litellm_db.fetch(
            "SELECT migration_name, finished_at, rolled_back_at "
            'FROM "_prisma_migrations"'
        )
        assert rows, "no rows in _prisma_migrations -- LiteLLM's schema never migrated"

        broken = [
            row["migration_name"]
            for row in rows
            if row["finished_at"] is None or row["rolled_back_at"] is not None
        ]
        assert not broken, f"migrations left in a bad state: {broken}"

    async def test_all_bundled_migrations_applied(self, litellm_db):
        """Catches a migration bundled in this image that never even
        attempted to run (not just one that failed partway), by
        cross-checking _prisma_migrations against the image's own
        migration source."""
        entries = list_litellm_container_dir(MIGRATIONS_DIR)
        # migration_lock.toml isn't a migration, filter to timestamp-prefixed dirs.
        bundled = {name for name in entries if name and name[0].isdigit()}

        rows = await litellm_db.fetch(
            'SELECT migration_name FROM "_prisma_migrations" WHERE finished_at IS NOT NULL'
        )
        applied = {row["migration_name"] for row in rows}
        missing = bundled - applied
        assert not missing, f"migrations bundled but never applied: {sorted(missing)}"
