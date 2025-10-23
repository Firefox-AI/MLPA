import base64

import httpx
from fastapi import HTTPException

from .config import GATEWAY_HEADERS, GATEWAY_USERS_URL


async def get_or_create_user(user_id: str):
	"""Returns user info from any-llm-gateway, creating the user if they don't exist.
	Args:
		user_id (str): The user ID to look up or create.
	Returns:
		[user_info: dict, was_created: bool]
	"""

	async with httpx.AsyncClient() as client:
		try:
			response = await client.get(
				f"{GATEWAY_USERS_URL}/{user_id}",
				headers=GATEWAY_HEADERS,
			)
			if response.status_code == 200:
				return [response.json(), False]

			if response.status_code == 404:
				create_response = await client.post(
					GATEWAY_USERS_URL,
					json={"user_id": user_id},
					headers=GATEWAY_HEADERS,
				)
				create_response.raise_for_status()
				return [create_response.json(), True]
			response.raise_for_status()
			return [response.json(), False]
		except httpx.HTTPStatusError as e:
			raise HTTPException(
				status_code=e.response.status_code,
				detail={"error": f"Error fetching user info: {e}"},
			)
		except Exception as e:
			raise HTTPException(
				status_code=500, detail={"error": f"Error fetching user info: {e}"}
			)


def b64decode_safe(data_b64: str, obj_name: str = "object") -> str:
	try:
		return base64.urlsafe_b64decode(data_b64)
	except Exception as e:
		raise HTTPException(status_code=400, detail={obj_name: f"Invalid Base64: {e}"})
