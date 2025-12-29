import sys

import asyncpg
from loguru import logger

from mlpa.core.config import env


class PGService:
    pg: asyncpg.Pool | None

    def __init__(self, db_name: str):
        self.db_name = db_name
        self.db_url = f"{env.PG_DB_URL.rstrip('/')}/{db_name}"
        self.connected = False
        self.pg = None

    async def _get_prepared_statement(self, conn: asyncpg.Connection, query: str):
        stmt_cache = getattr(conn, "_mlpa_stmt_cache", None)
        if stmt_cache is None:
            stmt_cache = {}
            conn._mlpa_stmt_cache = stmt_cache
        if query not in stmt_cache:
            stmt_cache[query] = await conn.prepare(query)
        return stmt_cache[query]

    async def connect(self):
        try:
            self.pg = await asyncpg.create_pool(
                self.db_url,
                min_size=env.PG_POOL_MIN_SIZE,
                max_size=env.PG_POOL_MAX_SIZE,
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

    def check_status(self):
        if self.pg is None or not self.connected:
            return False
        return not getattr(self.pg, "_closed", True)
