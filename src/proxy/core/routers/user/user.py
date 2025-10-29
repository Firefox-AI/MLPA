import httpx
from fastapi import APIRouter, HTTPException

from proxy.core.config import LITELLM_HEADERS, env

router = APIRouter()


@router.get("/{user_id}", tags=["User"])
async def user_info(user_id: str):
	if not user_id:
		raise HTTPException(status_code=400, detail="Missing user_id")

	async with httpx.AsyncClient() as client:
		params = {"end_user_id": user_id}
		response = await client.get(
			f"{env.LITELLM_API_BASE}/customer/info",
			params=params,
			headers=LITELLM_HEADERS,
		)
		user = response.json()

	if not user:
		raise HTTPException(status_code=404, detail="User not found")
	return user
