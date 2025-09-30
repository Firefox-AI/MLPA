import os
import asyncpg
from ..config import env

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
			print(f"Error connecting to database: {e}")

	async def disconnect(self):
		if self.connected:
			await self.pg.close()
			self.connected = False

	def check_status(self):
		if self.pg is None or not self.connected:
			return False
		return not self.pg.is_closed()
