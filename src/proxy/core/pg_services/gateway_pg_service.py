import httpx
from fastapi import Header, HTTPException

from ..classes import UserUpdatePayload
from ..config import GATEWAY_HEADERS, GATEWAY_USERS_URL, env
from .pg_service import PGService


class GatewayPGService(PGService):
	"""
	Service for interacting with any-llm-gateway's database and API.
	Uses REST API calls to manage users instead of direct database access.
	"""

	def __init__(self):
		super().__init__(env.GATEWAY_DB_NAME)

	async def get_user(self, user_id: str):
		"""Get user via any-llm-gateway REST API"""
		async with httpx.AsyncClient() as client:
			try:
				response = await client.get(
					f"{GATEWAY_USERS_URL}/{user_id}",
					headers=GATEWAY_HEADERS,
				)
				if response.status_code == 404:
					return None
				response.raise_for_status()
				return response.json()
			except Exception as e:
				raise HTTPException(
					status_code=500, detail={"error": f"Error fetching user: {e}"}
				)

	async def update_user(
		self, request: UserUpdatePayload, master_key: str = Header(...)
	):
		"""
		Update user via any-llm-gateway REST API
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

		async with httpx.AsyncClient() as client:
			try:
				response = await client.patch(
					f"{GATEWAY_USERS_URL}/{user_id}",
					headers=GATEWAY_HEADERS,
					json=update_data,
				)
				if response.status_code == 404:
					raise HTTPException(
						status_code=404, detail=f"User with user_id '{user_id}' not found."
					)
				response.raise_for_status()
				return response.json()
			except httpx.HTTPStatusError as e:
				if e.response.status_code == 404:
					raise HTTPException(
						status_code=404, detail=f"User with user_id '{user_id}' not found."
					)
				raise HTTPException(
					status_code=e.response.status_code,
					detail={"error": f"Error updating user: {e}"}
				)
			except Exception as e:
				raise HTTPException(
					status_code=500, detail={"error": f"Error updating user: {e}"}
				)
