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
