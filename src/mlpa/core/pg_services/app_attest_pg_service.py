from loguru import logger

from mlpa.core.config import env
from mlpa.core.pg_services.pg_service import PGService


class AppAttestPGService(PGService):
    def __init__(self):
        super().__init__(env.APP_ATTEST_DB_NAME)

    # Challenges #
    async def store_challenge(self, key_id_b64: str, challenge: str):
        try:
            await self.pg.execute(
                """
                INSERT INTO challenges (key_id_b64, challenge)
                VALUES ($1, $2)
                ON CONFLICT (key_id_b64) DO UPDATE SET
                challenge = EXCLUDED.challenge,
                created_at = NOW()
                """,
                key_id_b64,
                challenge,
            )
        except Exception as e:
            logger.error(f"Error storing challenge: {e}")

    async def get_challenge(self, key_id_b64: str) -> dict | None:
        try:
            return await self.pg.fetchrow(
                "SELECT challenge, created_at FROM challenges WHERE key_id_b64 = $1",
                key_id_b64,
            )
        except Exception as e:
            logger.error(f"Error retrieving challenge: {e}")

    async def delete_challenge(self, key_id_b64: str):
        try:
            await self.pg.execute(
                "DELETE FROM challenges WHERE key_id_b64 = $1", key_id_b64
            )
        except Exception as e:
            logger.error(f"Error deleting challenge: {e}")

    # Keys #
    async def store_key(self, key_id_b64: str, public_key_pem: str, counter: int):
        try:
            await self.pg.execute(
                """
                INSERT INTO public_keys (key_id_b64, public_key_pem, counter, updated_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (key_id_b64) DO UPDATE SET
                public_key_pem = EXCLUDED.public_key_pem,
                counter = EXCLUDED.counter,
                updated_at = NOW()
                """,
                key_id_b64,
                public_key_pem,
                counter,
            )
        except Exception as e:
            logger.error(f"Error storing key: {e}")

    async def get_key(self, key_id_b64: str) -> dict | None:
        try:
            record = await self.pg.fetchrow(
                """
                SELECT public_key_pem, counter FROM public_keys
                WHERE key_id_b64 = $1
                """,
                key_id_b64,
            )
            return record
        except Exception as e:
            logger.error(f"Error retrieving key: {e}")
            return None

    async def update_key_counter(self, key_id_b64: str, counter: int):
        try:
            await self.pg.execute(
                """
                UPDATE public_keys
                SET counter = $2,
                    updated_at = NOW()
                WHERE key_id_b64 = $1
                """,
                key_id_b64,
                counter,
            )
        except Exception as e:
            logger.error(f"Error updating key counter: {e}")

    async def delete_key(self, key_id_b64: str):
        try:
            await self.pg.execute(
                "DELETE FROM public_keys WHERE key_id_b64 = $1", key_id_b64
            )
        except Exception as e:
            logger.error(f"Error deleting key: {e}")
