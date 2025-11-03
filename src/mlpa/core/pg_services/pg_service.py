import os
import sys

import asyncpg

from mlpa.core.config import env


class PGService:
    pg: asyncpg.Connection

    def __init__(self, db_name: str):
        self.db_name = db_name
        self.db_url = os.path.join(env.PG_DB_URL, db_name)
        self.connected = False

    async def connect(self):
        try:
            self.pg = await asyncpg.connect(self.db_url)
            self.connected = True
            print(f"Connected to /{self.db_name}")
        except Exception as e:
            sys.exit(
                f"Couldn't connect to a database {self.db_name}, URL: {self.db_url}, error: {e}"
            )

    async def disconnect(self):
        if self.connected:
            await self.pg.close()
            self.connected = False

    def check_status(self):
        if self.pg is None or not self.connected:
            return False
        return not self.pg.is_closed()
