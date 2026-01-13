import sys
import weakref
from collections.abc import MutableMapping
from typing import Any

import asyncpg
from cachetools import LRUCache

from mlpa.core.config import env
from mlpa.core.logger import logger


class PGService:
    pg: asyncpg.Pool | None

    def __init__(self, db_name: str):
        self.db_name = db_name
        self.db_url = f"{env.PG_DB_URL.rstrip('/')}/{db_name}"
        self.connected = False
        self.pg = None
        self._stmt_caches = weakref.WeakKeyDictionary()
        self._stmt_caches_by_id: dict[int, MutableMapping[str, Any]] = {}

    def _create_stmt_cache(self) -> MutableMapping[str, Any]:
        """Create a prepared statement cache with LRU eviction."""
        return LRUCache(maxsize=env.PG_PREPARED_STMT_CACHE_MAX_SIZE)

    async def _get_prepared_statement(self, conn: asyncpg.Connection, query: str):
        stmt_cache = None
        try:
            stmt_cache = self._stmt_caches.get(conn)
        except TypeError:
            stmt_cache = None

        if stmt_cache is None:
            stmt_cache = self._stmt_caches_by_id.get(id(conn))

        if stmt_cache is None:
            stmt_cache = self._create_stmt_cache()
            try:
                self._stmt_caches[conn] = stmt_cache
            except TypeError:
                self._stmt_caches_by_id.setdefault(id(conn), stmt_cache)

        if query in stmt_cache:
            return stmt_cache[query]

        prepared_stmt = await conn.prepare(query)
        stmt_cache[query] = prepared_stmt
        return prepared_stmt

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
