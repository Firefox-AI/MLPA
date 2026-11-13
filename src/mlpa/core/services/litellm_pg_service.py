from fastapi import HTTPException

from mlpa.core.config import env
from mlpa.core.logger import logger
from mlpa.core.services.pg_service import PGService
from mlpa.core.ttl_cache import AsyncTTLCache


class LiteLLMPGService(PGService):
    """
    This service is primarily intended for updating user fields (directly via the DB) that are not supported by the free tier of LiteLLM.
    """

    def __init__(self):
        super().__init__(env.LITELLM_DB_NAME)
        # Only existing users are cached: a miss must reach the DB so a user
        # created moments ago (here or on another pod) is seen straight away.
        self._user_cache: AsyncTTLCache[str, dict] = AsyncTTLCache(
            "get_user",
            ttl_s=env.GET_USER_CACHE_TTL_S,
            maxsize=env.GET_USER_CACHE_MAX_SIZE,
        )
        self._user_counts_cache: AsyncTTLCache[str, dict] = AsyncTTLCache(
            "user_counts_by_service_type",
            ttl_s=env.USER_COUNTS_CACHE_TTL_S,
            maxsize=1,
        )

    async def get_user(self, user_id: str, *, fresh: bool = False) -> dict | None:
        """
        fresh=True skips any cached row and any in-flight lookup that may have
        started before a write (e.g. re-reading right after creating the user).
        """
        if fresh:
            self._user_cache.invalidate(user_id)
        user = await self._user_cache.get(user_id, lambda: self._fetch_user(user_id))
        # Copy so callers can't mutate the cached row.
        return dict(user) if user else None

    async def _fetch_user(self, user_id: str) -> dict | None:
        user = await self.pool.fetchrow(
            'SELECT * FROM "LiteLLM_EndUserTable" WHERE user_id = $1',
            user_id,
        )
        return dict(user) if user else None

    async def update_user_budget(self, user_id: str, budget_id: str) -> dict:
        """Update a user's budget by linking them to a different budget tier."""
        try:
            async with self.pool.acquire() as conn:
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
        finally:
            # Runs after the transaction has committed or rolled back.
            self._user_cache.invalidate(user_id)

    async def block_user(self, user_id: str, blocked: bool = True) -> dict:
        try:
            async with self.pool.acquire() as conn:
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
        finally:
            # Runs after the transaction has committed or rolled back.
            self._user_cache.invalidate(user_id)

    async def list_users(self, limit: int = 50, offset: int = 0) -> dict:
        try:
            # COUNT(*) + deep OFFSET full-scan the table, so use the admin-read budget.
            async with self.statement_timeout(env.PG_ADMIN_READ_TIMEOUT_MS) as conn:
                total = await conn.fetchval(
                    'SELECT COUNT(*) FROM "LiteLLM_EndUserTable"'
                )
                users = await conn.fetch(
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

        Cached per pod for USER_COUNTS_CACHE_TTL_S, since exact counts always
        scan the whole table.
        """
        counts = await self._user_counts_cache.get(
            "all", self._count_users_by_service_type
        )
        assert counts is not None
        return counts

    async def _count_users_by_service_type(self) -> dict:
        try:
            # GROUP BY split_part(...) is unindexable, so always a full scan: use
            # the admin-read budget.
            async with self.statement_timeout(env.PG_ADMIN_READ_TIMEOUT_MS) as conn:
                rows = await conn.fetch(
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

    async def list_managed_base_identities(
        self, managed_service_types: list[str]
    ) -> list[str]:
        """
        Return distinct base identities for cap-managed service types.

        The DISTINCT full-scan is startup reconciliation work, so it runs under
        the maintenance budget.
        """
        async with self.statement_timeout(
            env.PG_MAINTENANCE_STATEMENT_TIMEOUT_MS
        ) as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT split_part(user_id, ':', 1) AS base_identity
                FROM "LiteLLM_EndUserTable"
                WHERE position(':' in user_id) > 0
                  AND split_part(user_id, ':', 2) = ANY($1::text[])
                """,
                managed_service_types,
            )
        return [row["base_identity"] for row in rows if row.get("base_identity")]

    async def has_managed_user_rows(
        self, base_identity: str, managed_service_types: list[str]
    ) -> bool:
        """
        Return True if the base identity has any cap-managed LiteLLM end-user rows.
        """
        # Rows are always "{base_identity}:{service_type}" (get_or_create_user
        # rejects anything else), so the candidate ids can be built up front and
        # matched on the primary key instead of scanning with split_part.
        user_ids = [
            f"{base_identity}:{service_type}" for service_type in managed_service_types
        ]
        return bool(
            await self.pool.fetchval(
                """
                SELECT EXISTS(
                    SELECT 1
                    FROM "LiteLLM_EndUserTable"
                    WHERE user_id = ANY($1::text[])
                )
                """,
                user_ids,
            )
        )

    async def create_budget(self):
        """
        Create end user budgets from configuration.
        Loops through all service types in user_feature_budget config and creates/updates each budget.
        If a budget already exists, it will be overwritten with the new values.
        Returns a list of created/updated budget records.
        """

        for service_type, budget_config in env.service_type_config.items():
            try:
                # Fast single-row PK upsert: a plain autocommit call won't hit
                # the pool statement_timeout.
                await self.pool.fetchrow(
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
