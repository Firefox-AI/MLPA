import secrets
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from mlpa.core.classes import BudgetUpdatePayload
from mlpa.core.config import LITELLM_MASTER_AUTH_HEADERS, env
from mlpa.core.http_client import get_http_client
from mlpa.core.logger import logger
from mlpa.core.pg_services.services import litellm_pg
from mlpa.core.utils import raise_and_log

router = APIRouter()


def require_master_key(
    master_key: Annotated[str, Header(alias="master_key")],
) -> None:
    try:
        if not secrets.compare_digest(master_key, f"Bearer {env.MASTER_KEY}"):
            raise HTTPException(status_code=401, detail={"error": "Unauthorized"})
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Master key verification failed: {e}")
        raise HTTPException(status_code=401, detail={"error": "Unauthorized"})


@router.get("", tags=["User Management"])
async def list_users(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    _: Annotated[None, Depends(require_master_key)] = None,
):
    """List all users with pagination support."""
    return await litellm_pg.list_users(limit=limit, offset=offset)


@router.get("/{user_id}", tags=["User"])
async def user_info(user_id: str):
    if not user_id or user_id.strip() == "":
        raise HTTPException(status_code=404, detail="User not found")

    client = get_http_client()
    params = {"end_user_id": user_id}
    response = await client.get(
        f"{env.LITELLM_API_BASE}/customer/info",
        params=params,
        headers=LITELLM_MASTER_AUTH_HEADERS,
    )
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise_and_log(e, False, e.response.status_code, "Error fetching user info")
    user = response.json()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


@router.post("/{user_id}/budget", tags=["User Management"])
async def update_user_budget(
    user_id: str,
    payload: BudgetUpdatePayload,
    _: Annotated[None, Depends(require_master_key)] = None,
):
    """Update a user's budget tier by service type (e.g. ai-dev for higher limits)."""
    if not user_id or user_id.strip() == "":
        raise HTTPException(status_code=404, detail="User not found")
    if payload.service_type not in env.valid_service_types:
        raise HTTPException(
            status_code=422,
            detail={
                "error": f"Unknown service type: {payload.service_type}. "
                f"Valid values: {', '.join(env.valid_service_types)}"
            },
        )
    budget_id = env.user_feature_budget[payload.service_type]["budget_id"]
    user = await litellm_pg.update_user_budget(user_id, budget_id)
    return {
        "user_id": user["user_id"],
        "budget_id": user["budget_id"],
        "service_type": payload.service_type,
    }


@router.post("/{user_id}/block", tags=["User Management"])
async def block_user(
    user_id: str,
    _: Annotated[None, Depends(require_master_key)] = None,
):
    """Block a user by their user_id."""
    return await litellm_pg.block_user(user_id, blocked=True)


@router.post("/{user_id}/unblock", tags=["User Management"])
async def unblock_user(
    user_id: str,
    _: Annotated[None, Depends(require_master_key)] = None,
):
    """Unblock a user by their user_id."""
    return await litellm_pg.block_user(user_id, blocked=False)
