from fastapi import HTTPException

from mlpa.core.config import env
from mlpa.core.logger import logger
from mlpa.core.pg_services.litellm_pg_service import LiteLLMPGService
from mlpa.core.pg_services.pg_service import PGService


class AppAttestPGService(PGService):
    def __init__(self, litellm_pg: LiteLLMPGService):
        super().__init__(env.APP_ATTEST_DB_NAME)
        self.litellm_pg = litellm_pg

    # Challenges #
    async def store_challenge(self, key_id_b64: str, challenge: str):
        try:
            await self.pool.fetchval(
                """
                INSERT INTO challenges (key_id_b64, challenge)
                VALUES ($1, $2)
                ON CONFLICT (key_id_b64) DO UPDATE SET
                challenge = EXCLUDED.challenge,
                created_at = NOW()
                RETURNING 1
                """,
                key_id_b64,
                challenge,
            )
        except Exception as e:
            logger.error(f"Error storing challenge: {e}")

    async def get_challenge(self, key_id_b64: str) -> dict | None:
        try:
            return await self.pool.fetchrow(
                "SELECT challenge, created_at FROM challenges WHERE key_id_b64 = $1",
                key_id_b64,
            )
        except Exception as e:
            logger.error(f"Error retrieving challenge: {e}")

    async def delete_challenge(self, key_id_b64: str):
        try:
            await self.pool.fetchval(
                "DELETE FROM challenges WHERE key_id_b64 = $1 RETURNING 1",
                key_id_b64,
            )
        except Exception as e:
            logger.error(f"Error deleting challenge: {e}")

    # Keys #
    async def store_key(self, key_id_b64: str, public_key_pem: str, counter: int):
        try:
            await self.pool.execute(
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
            return await self.pool.fetchrow(
                "SELECT public_key_pem, counter FROM public_keys WHERE key_id_b64 = $1",
                key_id_b64,
            )
        except Exception as e:
            logger.error(f"Error retrieving key: {e}")
            return None

    async def update_key_counter(self, key_id_b64: str, counter: int):
        try:
            await self.pool.fetchval(
                """
                UPDATE public_keys
                SET counter = $2,
                    updated_at = NOW()
                WHERE key_id_b64 = $1 AND counter < $2
                RETURNING 1
                """,
                key_id_b64,
                counter,
            )
        except Exception as e:
            logger.error(f"Error updating key counter: {e}")

    async def delete_key(self, key_id_b64: str):
        try:
            await self.pool.execute(
                "DELETE FROM public_keys WHERE key_id_b64 = $1", key_id_b64
            )
        except Exception as e:
            logger.error(f"Error deleting key: {e}")

    async def ensure_capacity_state(self) -> None:
        """
        Seed the singleton capacity row, then reconcile the claim table.

        The seed is critical and fatal on failure: without the row every
        admission 500s, so a failure should crash startup rather than serve
        broken. Reconciliation is best-effort (see _reconcile_capacity_claims):
        if it fails the row still exists with a stale count and admissions work.
        """
        # Seed the singleton row (fatal on failure).
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO mlpa_user_capacity (id, max_identities, current_identities)
                    VALUES (1, $1, 0)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    env.MLPA_MAX_SIGNED_IN_USERS,
                )
                await conn.execute(
                    """
                    UPDATE mlpa_user_capacity
                    SET max_identities = $1,
                        updated_at = NOW()
                    WHERE id = 1
                    """,
                    env.MLPA_MAX_SIGNED_IN_USERS,
                )

        # Reconcile the claim table (best-effort).
        try:
            await self._reconcile_capacity_claims()
        except Exception as e:
            logger.error(
                f"Capacity reconciliation failed; serving with last-known "
                f"current_identities count: {e}"
            )

    async def _reconcile_capacity_claims(self) -> None:
        """Rebuild the claim table from LiteLLM and refresh current_identities."""
        managed_service_types = list(env.MLPA_CAPPED_SERVICE_TYPES)

        # Read from the litellm pool before opening the app_attest transaction:
        # doing it inside would leave the session idle-in-transaction across a
        # cross-pool await, where idle_in_transaction_session_timeout could reap it.
        base_identities = await self.litellm_pg.list_managed_base_identities(
            managed_service_types
        )

        # Bulk delete + insert scales with the user base and can exceed the tight
        # pool-wide statement_timeout. Statements run back-to-back (no inter-
        # statement await), so the raised statement_timeout alone suffices.
        async with self.statement_timeout(
            env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS
        ) as conn:
            # Serialize so concurrent app startups do not race on the claim table.
            await conn.fetchrow(
                "SELECT 1 FROM mlpa_user_capacity WHERE id = 1 FOR UPDATE"
            )

            # Blocked rows still count toward capacity.
            await conn.execute("DELETE FROM mlpa_user_capacity_identities")

            if base_identities:
                await conn.executemany(
                    """
                    INSERT INTO mlpa_user_capacity_identities (base_identity)
                    VALUES ($1)
                    """,
                    [(base_identity,) for base_identity in base_identities],
                )

            seeded_claims = await conn.fetchval(
                "SELECT COUNT(*) FROM mlpa_user_capacity_identities"
            )
            await conn.execute(
                """
                UPDATE mlpa_user_capacity
                SET current_identities = $1,
                    updated_at = NOW()
                WHERE id = 1
                """,
                seeded_claims,
            )

    async def admit_managed_base_identity(
        self, base_identity: str
    ) -> tuple[bool, bool]:
        """
        Admit a cap-managed base identity.

        Returns:
          (admitted, newly_claimed)
        """
        if not env.MLPA_ENFORCE_SIGNIN_CAP:
            return True, False

        async with self.admission_transaction() as conn:
            capacity_row = await conn.fetchrow(
                """
                SELECT max_identities, current_identities
                FROM mlpa_user_capacity
                WHERE id = 1
                FOR UPDATE
                """
            )
            if capacity_row is None:
                raise HTTPException(
                    status_code=500,
                    detail="Capacity state not initialized",
                )

            already_claimed = await conn.fetchval(
                """
                SELECT 1
                FROM mlpa_user_capacity_identities
                WHERE base_identity = $1
                """,
                base_identity,
            )
            if already_claimed:
                return True, False

            max_identities = int(capacity_row["max_identities"])
            current_identities = int(capacity_row["current_identities"])
            if current_identities >= max_identities:
                return False, False

            await conn.execute(
                """
                INSERT INTO mlpa_user_capacity_identities (base_identity)
                VALUES ($1)
                """,
                base_identity,
            )
            await conn.execute(
                """
                UPDATE mlpa_user_capacity
                SET current_identities = current_identities + 1,
                    updated_at = NOW()
                WHERE id = 1
                """
            )
            return True, True

    async def maybe_release_managed_base_identity_if_no_managed_users(
        self, base_identity: str
    ) -> None:
        """
        Release a claim if the base identity has no cap-managed LiteLLM end-user
        rows (blocked rows still count — delete/unblock+delete in LiteLLM to release).
        """
        if not env.MLPA_ENFORCE_SIGNIN_CAP:
            return

        managed_service_types = list(env.MLPA_CAPPED_SERVICE_TYPES)

        # Read the litellm state before opening the app_attest transaction: doing
        # it inside would hold the FOR UPDATE lock idle-in-transaction across a
        # cross-pool await, where idle_in_transaction_session_timeout could reap
        # it and abort the release, leaking the claim (mirrors ensure_capacity_state).
        has_managed_user_rows = await self.litellm_pg.has_managed_user_rows(
            base_identity,
            managed_service_types,
        )
        if has_managed_user_rows:
            return

        async with self.admission_transaction() as conn:
            capacity_row = await conn.fetchrow(
                """
                SELECT max_identities, current_identities
                FROM mlpa_user_capacity
                WHERE id = 1
                FOR UPDATE
                """
            )
            if capacity_row is None:
                return

            claimed = await conn.fetchval(
                """
                SELECT 1
                FROM mlpa_user_capacity_identities
                WHERE base_identity = $1
                """,
                base_identity,
            )
            if not claimed:
                return

            await conn.execute(
                """
                DELETE FROM mlpa_user_capacity_identities
                WHERE base_identity = $1
                """,
                base_identity,
            )
            await conn.execute(
                """
                UPDATE mlpa_user_capacity
                SET current_identities = GREATEST(current_identities - 1, 0),
                    updated_at = NOW()
                WHERE id = 1
                """
            )

    async def get_signup_cap_status(self) -> dict:
        """
        Managed signup capacity: distinct base identities with any capped service type row.
        Mirrors mlpa_user_capacity / mlpa_user_capacity_identities (updated at startup and on admit/release).
        """
        try:
            row = await self.pool.fetchrow(
                """
                    SELECT max_identities, current_identities, updated_at
                    FROM mlpa_user_capacity
                    WHERE id = 1
                    """
            )
        except Exception as e:
            logger.error(f"Error reading signup cap state: {e}")
            raise HTTPException(
                status_code=500,
                detail={"error": "Error reading signup cap state"},
            )

        if row is None:
            return {
                "enforce_signin_cap": env.MLPA_ENFORCE_SIGNIN_CAP,
                "capped_service_types": sorted(env.MLPA_CAPPED_SERVICE_TYPES),
                "max_signed_in_users": env.MLPA_MAX_SIGNED_IN_USERS,
                "current_managed_identities": None,
                "slots_remaining": None,
                "capacity_updated_at": None,
                "capacity_row_missing": True,
            }

        max_i = int(row["max_identities"])
        cur = int(row["current_identities"])
        updated_at = row["updated_at"]
        return {
            "enforce_signin_cap": env.MLPA_ENFORCE_SIGNIN_CAP,
            "capped_service_types": sorted(env.MLPA_CAPPED_SERVICE_TYPES),
            "max_signed_in_users": max_i,
            "current_managed_identities": cur,
            "slots_remaining": max(0, max_i - cur),
            "capacity_updated_at": updated_at.isoformat() if updated_at else None,
            "capacity_row_missing": False,
        }
