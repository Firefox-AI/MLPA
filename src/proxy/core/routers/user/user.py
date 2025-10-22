import httpx
from fastapi import APIRouter, HTTPException

from ...config import GATEWAY_HEADERS, GATEWAY_USERS_URL

router = APIRouter()


@router.get("/{user_id}", tags=["User"])
async def user_info(user_id: str):
	if not user_id:
		raise HTTPException(status_code=400, detail="Missing user_id")

	async with httpx.AsyncClient() as client:
		response = await client.get(
			f"{GATEWAY_USERS_URL}/{user_id}",
			headers=GATEWAY_HEADERS,
		)

		if response.status_code == 404:
			raise HTTPException(status_code=404, detail="User not found")

		response.raise_for_status()
		user = response.json()

	return user
