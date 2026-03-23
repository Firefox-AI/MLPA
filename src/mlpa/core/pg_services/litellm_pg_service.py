from fastapi import HTTPException

from mlpa.core.config import env
from mlpa.core.logger import logger
from mlpa.core.pg_services.pg_service import PGService


class LiteLLMPGService(PGService):
    """
    This service is primarily intended for updating user fields (directly via the DB) that are not supported by the free tier of LiteLLM.
    """

    def __init__(self):
        super().__init__(env.LITELLM_DB_NAME)

    async def get_user(self, user_id: str):
        user = await self.pg.fetchrow(
            'SELECT * FROM "LiteLLM_EndUserTable" WHERE user_id = $1',
            user_id,
        )
        return dict(user) if user else None

    async def update_user_budget(self, user_id: str, budget_id: str) -> dict:
        """Update a user's budget by linking them to a different budget tier."""
        try:
            async with self.pg.acquire() as conn:
                async with conn.transaction():
                    updated_user_record = await conn.fetchrow(
                        'UPDATE "LiteLLM_EndUserTable" SET "budget_id" = $1 WHERE user_id = $2 RETURNING *',
                        budget_id,
                        user_id,
                    )

                    if updated_user_record is None:
                        logger.error(f"User {user_id} not found for budget update.")
                        raise HTTPException(status_code=404, detail="User not found.")

                    logger.info(
                        f"User {user_id} budget updated to {budget_id} successfully."
                    )
                    return dict(updated_user_record)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error updating budget for user {user_id}: {e}")
            raise HTTPException(
                status_code=500, detail={"error": "Error updating user budget"}
            )

    async def block_user(self, user_id: str, blocked: bool = True) -> dict:
        try:
            async with self.pg.acquire() as conn:
                async with conn.transaction():
                    updated_user_record = await conn.fetchrow(
                        'UPDATE "LiteLLM_EndUserTable" SET "blocked" = $1 WHERE user_id = $2 RETURNING *',
                        blocked,
                        user_id,
                    )

                    if updated_user_record is None:
                        logger.error(
                            f"User {user_id} not found for blocking/unblocking."
                        )
                        raise HTTPException(status_code=404, detail="User not found.")

                    logger.info(
                        f"User {user_id} {'blocked' if blocked else 'unblocked'} successfully."
                    )
                    return dict(updated_user_record)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error blocking/unblocking user {user_id}: {e}")
            raise HTTPException(
                status_code=500, detail={"error": "Error updating user"}
            )

    async def list_users(self, limit: int = 50, offset: int = 0) -> dict:
        try:
            total = await self.pg.fetchval(
                'SELECT COUNT(*) FROM "LiteLLM_EndUserTable"'
            )
            users = await self.pg.fetch(
                'SELECT * FROM "LiteLLM_EndUserTable" ORDER BY user_id LIMIT $1 OFFSET $2',
                limit,
                offset,
            )

            return {
                "users": [dict(user) for user in users],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        except Exception as e:
            logger.error(f"Error listing users: {e}")
            raise HTTPException(
                status_code=500, detail={"error": "Error listing users"}
            )

    async def count_users_by_service_type(self) -> dict:
        """
        Return total user counts grouped by service_type.

        LiteLLM stores users by `user_id` which is formatted as:
        `{base_user_id}:{service_type}`.
        """
        try:
            rows = await self.pg.fetch(
                """
                SELECT
                    split_part(user_id, ':', 2) AS service_type,
                    COUNT(*)::int AS total_users
                FROM "LiteLLM_EndUserTable"
                WHERE position(':' in user_id) > 0
                GROUP BY service_type
                """
            )

            service_type_counts: dict[str, int] = {}
            for row in rows:
                service_type = row.get("service_type")
                if not service_type:
                    continue
                service_type_counts[str(service_type)] = int(row.get("total_users", 0))

            total_users = sum(service_type_counts.values())
            return {
                "service_type_counts": service_type_counts,
                "total_users": int(total_users),
            }
        except Exception as e:
            logger.error(f"Error counting users by service type: {e}")
            raise HTTPException(
                status_code=500,
                detail={"error": "Error counting users by service type"},
            )

    async def create_budget(self):
        """
        Create end user budgets from configuration.
        Loops through all service types in user_feature_budget config and creates/updates each budget.
        If a budget already exists, it will be overwritten with the new values.
        Returns a list of created/updated budget records.
        """
        budgets_created = []
        user_feature_budgets = env.user_feature_budget

        for service_type, budget_config in user_feature_budgets.items():
            try:
                await self.pg.fetchrow(
                    """
                    INSERT INTO "LiteLLM_BudgetTable"
                    (budget_id, max_budget, rpm_limit, tpm_limit, budget_duration, created_at, updated_at, created_by, updated_by)
                    VALUES ($1, $2, $3, $4, $5, NOW(), NOW(), $6, $6)
                    ON CONFLICT (budget_id) DO UPDATE SET
                    max_budget = EXCLUDED.max_budget,
                    rpm_limit = EXCLUDED.rpm_limit,
                    tpm_limit = EXCLUDED.tpm_limit,
                    budget_duration = EXCLUDED.budget_duration,
                    updated_at = NOW(),
                    updated_by = EXCLUDED.updated_by
                    RETURNING *
                    """,
                    budget_config["budget_id"],
                    budget_config["max_budget"],
                    budget_config["rpm_limit"],
                    budget_config["tpm_limit"],
                    budget_config["budget_duration"],
                    "default_user_id",
                )
                logger.info(
                    f"Budget created/updated: budget_id={budget_config['budget_id']}, "
                    f"service_type={service_type}, max_budget={budget_config['max_budget']}"
                )
            except Exception as e:
                logger.error(
                    f"Error creating budget for service_type={service_type}, budget_id={budget_config.get('budget_id', 'unknown')}: {e}"
                )

    async def ensure_capacity_state(self) -> None:
        """
        Ensure the singleton capacity row and base-identity claim table exist.

        Reconciles the claim table with current LiteLLM end-user rows for
        cap-managed service types on every startup (blocked rows included) so
        the counter reflects reality after external writes and config changes.
        """
        managed_service_types = list(env.MLPA_CAPPED_SERVICE_TYPES)

        async with self.pg.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mlpa_user_capacity (
                        id SMALLINT PRIMARY KEY CHECK (id = 1),
                        max_identities BIGINT NOT NULL CHECK (max_identities >= 0),
                        current_identities BIGINT NOT NULL CHECK (current_identities >= 0),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                await conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS mlpa_user_capacity_identities (
                        base_identity TEXT PRIMARY KEY,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )

                await conn.execute(
                    """
                    INSERT INTO mlpa_user_capacity (id, max_identities, current_identities)
                    VALUES (1, $1, 0)
                    ON CONFLICT (id) DO UPDATE SET
                        max_identities = EXCLUDED.max_identities,
                        updated_at = NOW()
                    """,
                    env.MLPA_MAX_SIGNED_IN_USERS,
                )

                # Serialize seeding and reconciliation so concurrent app startups
                # do not race on the claim table.
                await conn.fetchrow(
                    "SELECT 1 FROM mlpa_user_capacity WHERE id = 1 FOR UPDATE"
                )

                # Rebuild claims from LiteLLM so the counter matches reality after deletes
                # or manual DB edits. Blocked rows still count toward capacity.
                await conn.execute("DELETE FROM mlpa_user_capacity_identities")
                await conn.execute(
                    """
                    INSERT INTO mlpa_user_capacity_identities (base_identity)
                    SELECT DISTINCT split_part(user_id, ':', 1) AS base_identity
                    FROM "LiteLLM_EndUserTable"
                    WHERE position(':' in user_id) > 0
                      AND split_part(user_id, ':', 2) = ANY($1::text[])
                    """,
                    managed_service_types,
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

        async with self.pg.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL lock_timeout = '{env.MLPA_ADMISSION_LOCK_TIMEOUT_MS}ms'"
                )
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

        async with self.pg.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    f"SET LOCAL lock_timeout = '{env.MLPA_ADMISSION_LOCK_TIMEOUT_MS}ms'"
                )
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

                has_managed_user_rows = await conn.fetchval(
                    """
                    SELECT EXISTS(
                        SELECT 1
                        FROM "LiteLLM_EndUserTable"
                        WHERE position(':' in user_id) > 0
                          AND split_part(user_id, ':', 1) = $1
                          AND split_part(user_id, ':', 2) = ANY($2::text[])
                    )
                    """,
                    base_identity,
                    managed_service_types,
                )
                if has_managed_user_rows:
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
