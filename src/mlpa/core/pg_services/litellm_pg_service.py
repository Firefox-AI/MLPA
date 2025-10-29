from fastapi import Header, HTTPException

from mlpa.core.classes import UserUpdatePayload
from mlpa.core.config import env
from mlpa.core.pg_services.pg_service import PGService


class LiteLLMPGService(PGService):
	"""
	This service is primarily intended for updating user fields (directly via the DB) that are not supported by the free tier of LiteLLM.
	"""

	def __init__(self):
		super().__init__(env.LITELLM_DB_NAME)

	async def get_user(self, user_id: str):
		query = 'SELECT * FROM "LiteLLM_EndUserTable" WHERE user_id = $1'
		user = await self.pg.fetchrow(query, user_id)
		return dict(user) if user else None

	async def update_user(
		self, request: UserUpdatePayload, master_key: str = Header(...)
	):
		"""
		Allow updating the user's (End User/Customer)'s information
		Free tier of LiteLLM does not support this, so updating the DB directly
		is a workaround.
		example POST body: {
		        "user_id": "test-user-32",
		        "blocked": false,
		        "budget_id": null,
		        "alias": null
		}
		"""
		if master_key != f"Bearer {env.MASTER_KEY}":
			raise HTTPException(status_code=401, detail={"error": "Unauthorized"})

		update_data = request.model_dump(exclude_unset=True)
		user_id = update_data.pop("user_id", request.user_id)

		if not update_data:
			return {"status": "no fields to update", "user_id": user_id}

		updated_user_record = None
		try:
			set_clause = ", ".join(
				[f'"{key}" = ${i + 1}' for i, key in enumerate(update_data.keys())]
			)
			values = list(update_data.values())
			where_value_index = len(values) + 1

			query = f'UPDATE "LiteLLM_EndUserTable" SET {set_clause} WHERE user_id = ${where_value_index} RETURNING *'
			updated_user_record = await self.pg.fetchrow(query, *values, user_id)
		except Exception as e:
			raise HTTPException(
				status_code=500, detail={"error": f"Error updating user: {e}"}
			)

		if updated_user_record is None:
			raise HTTPException(
				status_code=404, detail=f"User with user_id '{user_id}' not found."
			)

		return dict(updated_user_record)
