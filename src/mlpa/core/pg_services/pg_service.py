import asyncio
import sys
from typing import cast

import asyncpg

from mlpa.core.config import env
from mlpa.core.logger import logger


class PGService:
    pg: asyncpg.Pool | None

    def __init__(self, db_name: str):
        self.db_name = db_name
        self.db_url = f"{cast(str, env.PG_DB_URL).rstrip('/')}/{db_name}"
        self.connected = False
        self.pg = None

    @property
    def pool(self) -> asyncpg.Pool:
        return cast(asyncpg.Pool, self.pg)

    async def connect(self):
        try:
            self.pg = await asyncpg.create_pool(
                self.db_url,
                min_size=env.PG_POOL_MIN_SIZE,
                max_size=env.PG_POOL_MAX_SIZE,
                statement_cache_size=env.PG_PREPARED_STMT_CACHE_MAX_SIZE,
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

    async def ping(self, timeout_s: float | None = None) -> bool:
        """Run a bounded live query to prove the pool can serve a request."""
        if self.pg is None or not self.connected:
            return False
        try:
            async with asyncio.timeout(timeout_s or env.READINESS_CHECK_TIMEOUT_S):
                await self.pool.fetchval("SELECT 1")
            return True
        except Exception:
            logger.warning(f"readiness ping failed for /{self.db_name}")
            logger.debug(
                f"readiness ping failure details for /{self.db_name}", exc_info=True
            )
            return False
