import os
import sys

import asyncpg
from loguru import logger

from mlpa.core.config import env


class PGService:
    pg: asyncpg.Pool | None

    def __init__(self, db_name: str):
        self.db_name = db_name
        self.db_url = os.path.join(env.PG_DB_URL, db_name)
        self.connected = False
        self.pg = None

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
