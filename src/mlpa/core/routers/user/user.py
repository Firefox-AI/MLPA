from fastapi import APIRouter, HTTPException

from mlpa.core.config import LITELLM_MASTER_AUTH_HEADERS, env
from mlpa.core.http_client import get_http_client

router = APIRouter()


@router.get("/{user_id}", tags=["User"])
async def user_info(user_id: str):
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user_id")

    client = get_http_client()
    params = {"end_user_id": user_id}
    response = await client.get(
        f"{env.LITELLM_API_BASE}/customer/info",
        params=params,
        headers=LITELLM_MASTER_AUTH_HEADERS,
    )
    user = response.json()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user
