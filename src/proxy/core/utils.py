import base64

import httpx
from fastapi import HTTPException

from .config import LITELLM_HEADERS, env


async def get_or_create_user(user_id: str):
	"""Returns user info from LiteLLM, creating the user if they don't exist.
	Args:
		user_id (str): The user ID to look up or create.
	Returns:
		[user_info: dict, was_created: bool]
	"""

	async with httpx.AsyncClient() as client:
		try:
			params = {"end_user_id": user_id}
			response = await client.get(
				f"{env.LITELLM_API_BASE}/customer/info",
				params=params,
				headers=LITELLM_HEADERS,
			)
			user = response.json()
			if not user.get("user_id"):
				# add budget details or budget_id if necessary
				await client.post(
					f"{env.LITELLM_API_BASE}/customer/new",
					json={"user_id": user_id},
					headers=LITELLM_HEADERS,
				)
				response = await client.get(
					f"{env.LITELLM_API_BASE}/customer/info",
					params=params,
					headers=LITELLM_HEADERS,
				)
				return [response.json(), True]
			return [user, False]
		except Exception as e:
			raise HTTPException(
				status_code=500, detail={"error": f"Error fetching user info: {e}"}
			)


def b64decode_safe(data_b64: str, obj_name: str = "object") -> str:
	try:
		return base64.urlsafe_b64decode(data_b64)
	except Exception as e:
		raise HTTPException(status_code=400, detail={obj_name: f"Invalid Base64: {e}"})
