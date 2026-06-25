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
            server_settings = {
                "statement_timeout": f"{self.statement_timeout_ms}ms",
                "idle_in_transaction_session_timeout": (
                    f"{env.PG_IDLE_IN_TX_TIMEOUT_MS}ms"
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
    async def _timed_transaction(
        self,
        statement_timeout_ms: int,
        idle_in_tx_timeout_ms: int | None = None,
        lock_timeout_ms: int | None = None,
    ):
        """
        Yield a connection in a transaction with statement_timeout (and
        optionally idle_in_transaction_session_timeout / lock_timeout) set via
        SET LOCAL, so the connection reverts to the pool defaults on release.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL statement_timeout = '{statement_timeout_ms}ms'"
                )
                if idle_in_tx_timeout_ms is not None:
                    await conn.execute(
                        f"SET LOCAL idle_in_transaction_session_timeout = '{idle_in_tx_timeout_ms}ms'"
                    )
                if lock_timeout_ms is not None:
                    await conn.execute(
                        f"SET LOCAL lock_timeout = '{lock_timeout_ms}ms'"
                    )
                yield conn

    @asynccontextmanager
    async def statement_timeout(self, timeout_ms: int):
        """
        Raise statement_timeout for a transaction that legitimately exceeds the
        tight pool default (e.g. unindexable full-table scans). idle-in-tx is
        lifted to match, so the pool reaper can't abort it if an await lands
        between statements.
        """
        async with self._timed_transaction(
            timeout_ms, idle_in_tx_timeout_ms=timeout_ms
        ) as conn:
            yield conn

    @asynccontextmanager
    async def admission_transaction(self):
        """
        Signup-capacity admission path. Bounds the FOR UPDATE wait on the
        capacity row with lock_timeout, and sets statement_timeout above it so
        the wait is governed by lock_timeout rather than the tight pool default
        (Postgres counts lock-wait toward statement_timeout).
        """
        lock_ms = env.MLPA_ADMISSION_LOCK_TIMEOUT_MS
        stmt_ms = lock_ms + env.PG_STATEMENT_TIMEOUT_MS
        async with self._timed_transaction(
            stmt_ms, idle_in_tx_timeout_ms=stmt_ms, lock_timeout_ms=lock_ms
        ) as conn:
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
