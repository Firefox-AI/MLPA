from typing import cast

from fastapi import HTTPException

from mlpa.core.config import (
    GUEST_CAPACITY_ID,
    USE_OTARI,
    USER_CAPACITY_ID,
    capacity_settings,
    env,
)
from mlpa.core.logger import logger
from mlpa.core.services.litellm_pg_service import LiteLLMPGService
from mlpa.core.services.otari_service import OtariService
from mlpa.core.services.pg_service import PGService


class AppAttestPGService(PGService):
    def __init__(self, litellm_pg: LiteLLMPGService | OtariService):
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
        Seed the user and guest capacity rows, then reconcile the claim table.

        The user seed is fatal on failure: without the row every admission 500s,
        so we let it crash startup. The guest seed only logs, so a missed
        c41d7e2a9f10 migration (CHECK id = 1) breaks guests, not every pod.
        Reconciliation is best-effort — if it fails the row still holds a stale
        count and admissions keep working.
        """
        await self._seed_capacity_row(USER_CAPACITY_ID)
        try:
            await self._seed_capacity_row(GUEST_CAPACITY_ID)
        except Exception as e:
            logger.error(f"Guest capacity seed failed, is alembic at head? {e}")

        try:
            await self._reconcile_capacity_claims()
        except Exception as e:
            logger.error(
                f"Capacity reconciliation failed; serving with last-known "
                f"current_identities count: {e}"
            )

    async def _seed_capacity_row(self, capacity_id: int) -> None:
        _enforced, max_identities, _types = capacity_settings(capacity_id)
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO mlpa_user_capacity (id, max_identities, current_identities)
                VALUES ($1, $2, 0)
                ON CONFLICT (id) DO UPDATE SET
                    max_identities = EXCLUDED.max_identities,
                    updated_at = NOW()
                """,
                capacity_id,
                max_identities,
            )

    async def _reconcile_capacity_claims(self) -> None:
        """
        Rebuild user claims from LiteLLM and refresh both current_identities.

        Guest claims are not rebuilt: their created_at drives the infra cleanup,
        so they stay as they are and only the guest counter is recounted.
        """
        if USE_OTARI:
            # Under Otari the claim table is the record of who was admitted: Otari
            # creates an end user on its first request and MLPA never looks users up,
            # so there is nothing to rebuild it from. Both counters are recounted,
            # so slots freed by deleting claims come back.
            async with self.pool.acquire() as conn:
                async with conn.transaction():
                    await self._recount_capacity(conn, USER_CAPACITY_ID)
                    await self._recount_capacity(conn, GUEST_CAPACITY_ID)
            return
        managed_service_types = list(env.MLPA_CAPPED_SERVICE_TYPES)

        # Read the litellm pool before opening the app_attest transaction: doing
        # it inside would leave the session idle-in-transaction across the
        # cross-pool await, where idle_in_transaction_session_timeout could reap it.
        # Only LiteLLM gets here: under Otari the method returned above.
        base_identities = await cast(
            LiteLLMPGService, self.litellm_pg
        ).list_managed_base_identities(managed_service_types)

        # The bulk delete + insert grows with the user base, so run it under the
        # raised maintenance budget rather than the tight pool default.
        async with self.statement_timeout(
            env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS
        ) as conn:
            # Serialize so concurrent app startups do not race on the claim table.
            await conn.fetchrow(
                f"SELECT 1 FROM mlpa_user_capacity WHERE id = {USER_CAPACITY_ID} FOR UPDATE"
            )

            # Blocked rows still count toward capacity.
            await conn.execute(
                f"DELETE FROM mlpa_user_capacity_identities WHERE capacity_id = {USER_CAPACITY_ID}"
            )

            if base_identities:
                await conn.executemany(
                    """
                    INSERT INTO mlpa_user_capacity_identities (base_identity, capacity_id)
                    VALUES ($1, $2)
                    """,
                    [
                        (base_identity, USER_CAPACITY_ID)
                        for base_identity in base_identities
                    ],
                )

            seeded_claims = await conn.fetchval(
                f"SELECT COUNT(*) FROM mlpa_user_capacity_identities WHERE capacity_id = {USER_CAPACITY_ID}"
            )
            await conn.execute(
                f"""
                UPDATE mlpa_user_capacity
                SET current_identities = $1,
                    updated_at = NOW()
                WHERE id = {USER_CAPACITY_ID}
                """,
                seeded_claims,
            )

            await self._recount_capacity(conn, GUEST_CAPACITY_ID)

    async def _recount_capacity(self, conn, capacity_id: int) -> None:
        """Set a capacity row's current_identities to its number of claims."""
        # Lock first so the count below sees every committed admission.
        await conn.fetchrow(
            "SELECT 1 FROM mlpa_user_capacity WHERE id = $1 FOR UPDATE", capacity_id
        )
        await conn.execute(
            """
            UPDATE mlpa_user_capacity
            SET current_identities = (
                    SELECT COUNT(*) FROM mlpa_user_capacity_identities
                    WHERE capacity_id = $1
                ),
                updated_at = NOW()
            WHERE id = $1
            """,
            capacity_id,
        )

    async def admit_managed_base_identity(
        self, base_identity: str, capacity_id: int = USER_CAPACITY_ID
    ) -> tuple[bool, bool]:
        """
        Admit a cap-managed base identity into the given capacity row.

        Returns:
          (admitted, newly_claimed)
        """
        enforced, _max, _types = capacity_settings(capacity_id)
        if not enforced:
            return True, False

        # Most requests come from identities already admitted: answer those without
        # taking the capacity row's lock, which every new admission serializes on.
        if await self.pool.fetchval(
            """
            SELECT 1 FROM mlpa_user_capacity_identities
            WHERE base_identity = $1 AND capacity_id = $2
            """,
            base_identity,
            capacity_id,
        ):
            return True, False

        async with self.admission_transaction() as conn:
            capacity_row = await conn.fetchrow(
                """
                SELECT max_identities, current_identities
                FROM mlpa_user_capacity
                WHERE id = $1
                FOR UPDATE
                """,
                capacity_id,
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
                WHERE base_identity = $1 AND capacity_id = $2
                """,
                base_identity,
                capacity_id,
            )
            if already_claimed:
                return True, False

            max_identities = int(capacity_row["max_identities"])
            current_identities = int(capacity_row["current_identities"])
            if current_identities >= max_identities:
                return False, False

            await conn.execute(
                """
                INSERT INTO mlpa_user_capacity_identities (base_identity, capacity_id)
                VALUES ($1, $2)
                """,
                base_identity,
                capacity_id,
            )
            await conn.execute(
                """
                UPDATE mlpa_user_capacity
                SET current_identities = current_identities + 1,
                    updated_at = NOW()
                WHERE id = $1
                """,
                capacity_id,
            )
            return True, True

    async def maybe_release_managed_base_identity_if_no_managed_users(
        self, base_identity: str, capacity_id: int = USER_CAPACITY_ID
    ) -> None:
        """
        Release a claim if the base identity has no cap-managed LiteLLM end-user
        rows (blocked rows still count — delete/unblock+delete in LiteLLM to release).
        """
        enforced, _max, service_types = capacity_settings(capacity_id)
        if not enforced:
            return

        managed_service_types = list(service_types)

        # Read the litellm state before opening the app_attest transaction (same
        # cross-pool idle-in-transaction risk as ensure_capacity_state); reaping
        # the session here would abort the release and leak the claim.
        # Only the LiteLLM path releases claims (utils.get_or_create_user).
        has_managed_user_rows = await cast(
            LiteLLMPGService, self.litellm_pg
        ).has_managed_user_rows(
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
                WHERE id = $1
                FOR UPDATE
                """,
                capacity_id,
            )
            if capacity_row is None:
                return

            claimed = await conn.fetchval(
                """
                SELECT 1
                FROM mlpa_user_capacity_identities
                WHERE base_identity = $1 AND capacity_id = $2
                """,
                base_identity,
                capacity_id,
            )
            if not claimed:
                return

            await conn.execute(
                """
                DELETE FROM mlpa_user_capacity_identities
                WHERE base_identity = $1 AND capacity_id = $2
                """,
                base_identity,
                capacity_id,
            )
            await conn.execute(
                """
                UPDATE mlpa_user_capacity
                SET current_identities = GREATEST(current_identities - 1, 0),
                    updated_at = NOW()
                WHERE id = $1
                """,
                capacity_id,
            )

    async def get_signup_cap_status(self) -> dict:
        """
        Managed signup capacity: distinct base identities with any capped service type row,
        plus the guest counter (row 2).
        Mirrors mlpa_user_capacity / mlpa_user_capacity_identities (updated at startup and on admit/release).
        """
        try:
            rows = await self.pool.fetch(
                """
                    SELECT id, max_identities, current_identities, updated_at
                    FROM mlpa_user_capacity
                    """
            )
        except Exception as e:
            logger.error(f"Error reading signup cap state: {e}")
            raise HTTPException(
                status_code=500,
                detail={"error": "Error reading signup cap state"},
            )

        by_id = {r["id"]: r for r in rows}
        row = by_id.get(USER_CAPACITY_ID)
        guest_row = by_id.get(GUEST_CAPACITY_ID)
        guests = {
            "enforce_guest_cap": env.MLPA_ENFORCE_GUEST_CAP,
            "guest_service_types": list(env.MLPA_GUEST_SERVICE_TYPES),
            "max_guests": int(guest_row["max_identities"])
            if guest_row
            else env.MLPA_MAX_GUESTS,
            "current_guests": int(guest_row["current_identities"]) if guest_row else 0,
        }

        if row is None:
            return {
                "enforce_signin_cap": env.MLPA_ENFORCE_SIGNIN_CAP,
                "capped_service_types": sorted(env.MLPA_CAPPED_SERVICE_TYPES),
                "max_signed_in_users": env.MLPA_MAX_SIGNED_IN_USERS,
                "current_managed_identities": None,
                "slots_remaining": None,
                "capacity_updated_at": None,
                "capacity_row_missing": True,
                **guests,
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
            **guests,
        }
