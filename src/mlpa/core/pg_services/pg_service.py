import asyncio
import sys
from contextlib import asynccontextmanager
from typing import cast

import asyncpg

from mlpa.core.config import env
from mlpa.core.logger import logger


class PGService:
    pg: asyncpg.Pool | None

    def __init__(self, db_name: str, statement_timeout_ms: int | None = None):
        self.db_name = db_name
        self.db_url = f"{cast(str, env.PG_DB_URL).rstrip('/')}/{db_name}"
        # Pool-wide server-enforced statement timeout. Subclasses (or a future
        # third pool) may override per-DB; defaults to the global config value.
        self.statement_timeout_ms = (
            statement_timeout_ms
            if statement_timeout_ms is not None
            else env.PG_STATEMENT_TIMEOUT_MS
        )
        self.connected = False
        self.pg = None

    @property
    def pool(self) -> asyncpg.Pool:
        return cast(asyncpg.Pool, self.pg)

    async def connect(self):
        try:
            # Applied at connect and automatically re-applied on every reconnect,
            # so the timeout is durable across the pool's lifetime without
            # touching call sites. Values are passed as bare-integer ms strings.
            server_settings = {
                "statement_timeout": str(self.statement_timeout_ms),
                "idle_in_transaction_session_timeout": str(
                    env.PG_IDLE_IN_TX_TIMEOUT_MS
                ),
                "application_name": f"mlpa:{self.db_name}",
            }
            self.pg = await asyncpg.create_pool(
                self.db_url,
                min_size=env.PG_POOL_MIN_SIZE,
                max_size=env.PG_POOL_MAX_SIZE,
                statement_cache_size=env.PG_PREPARED_STMT_CACHE_MAX_SIZE,
                server_settings=server_settings,
                command_timeout=env.PG_COMMAND_TIMEOUT_S,
            )
            self.connected = True
            logger.info(f"Connected to /{self.db_name}")
        except Exception as e:
            sys.exit(
                f"Couldn't connect to a database {self.db_name}, URL: {self.db_url}, error: {e}"
            )

    async def disconnect(self):
        if self.connected and self.pg is not None:
            await self.pg.close()
            self.connected = False

    @asynccontextmanager
    async def maintenance_transaction(self):
        """
        Yield a connection inside a transaction whose statement and
        idle-in-transaction timeouts are raised to the maintenance value.

        For known-heavy startup work (capacity reconciliation, budget upsert)
        that legitimately exceeds the tight pool-wide statement_timeout. Both
        GUCs are raised via SET LOCAL: statement_timeout for slow statements,
        and idle_in_transaction_session_timeout because such work may await
        other queries between statements without the session being reaped.
        """
        timeout_ms = env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f"SET LOCAL statement_timeout = '{timeout_ms}'")
                await conn.execute(
                    f"SET LOCAL idle_in_transaction_session_timeout = '{timeout_ms}'"
                )
                yield conn

    async def ping(self, timeout_s: float | None = None) -> bool:
        """Run a bounded live query to prove the pool can serve a request."""
        if self.pg is None or not self.connected:
            return False
        if timeout_s is None:
            timeout_s = env.READINESS_CHECK_TIMEOUT_S
        try:
            async with asyncio.timeout(timeout_s):
                await self.pool.fetchval("SELECT 1")
            return True
        except Exception:
            logger.warning(f"readiness ping failed for /{self.db_name}")
            logger.debug(
                f"readiness ping failure details for /{self.db_name}", exc_info=True
            )
            return False
