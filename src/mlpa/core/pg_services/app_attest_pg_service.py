from mlpa.core.config import env
from mlpa.core.pg_services.pg_service import PGService


class AppAttestPGService(PGService):
	def __init__(self):
		super().__init__(env.APP_ATTEST_DB_NAME)

	# Challenges #
	async def store_challenge(self, key_id: str, challenge: str):
		try:
			await self.pg.execute(
				"""
				INSERT INTO challenges (key_id, challenge)
				VALUES ($1, $2)
				ON CONFLICT (key_id) DO UPDATE SET
				challenge = EXCLUDED.challenge,
				created_at = NOW()
				""",
				key_id,
				challenge,
			)
		except Exception as e:
			print(f"Error storing challenge: {e}")

	async def get_challenge(self, key_id: str) -> dict | None:
		try:
			return await self.pg.fetchrow(
				"SELECT challenge, created_at FROM challenges WHERE key_id = $1", key_id
			)
		except Exception as e:
			print(f"Error retrieving challenge: {e}")

	async def delete_challenge(self, key_id: str):
		try:
			await self.pg.execute("DELETE FROM challenges WHERE key_id = $1", key_id)
		except Exception as e:
			print(f"Error deleting challenge: {e}")

	# Keys #
	async def store_key(self, key_id: str, public_key: str):
		try:
			await self.pg.execute(
				"""
				INSERT INTO public_keys (key_id, public_key)
				VALUES ($1, $2)
				""",
				key_id,
				public_key,
			)
		except Exception as e:
			print(f"Error storing key: {e}")

	async def get_key(self, key_id: str) -> str | None:
		try:
			record = await self.pg.fetchrow(
				"""
				SELECT public_key FROM public_keys
				WHERE key_id = $1
				""",
				key_id,
			)
			if record:
				return record["public_key"]
			return None
		except Exception as e:
			print(f"Error retrieving key: {e}")
			return None

	async def delete_key(self, key_id: str):
		try:
			await self.pg.execute("DELETE FROM public_keys WHERE key_id = $1", key_id)
		except Exception as e:
			print(f"Error deleting key: {e}")
