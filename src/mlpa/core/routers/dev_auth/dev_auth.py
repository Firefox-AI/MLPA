import secrets

from fastapi import APIRouter, HTTPException

from mlpa.core.config import env
from mlpa.core.routers.fxa import fxa_auth

router = APIRouter()


async def auth_with_key(
    x_dev_authorization: str | None,
    authorization: str | None,
) -> dict:
    """
    Authenticate using x-dev-authorization key + FxA.
    Validates the experimentation token and returns FxA user profile.
    Raises HTTPException(401) on failure.
    """
    if not x_dev_authorization or not authorization:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization header (FxA required when x-dev-authorization is present)",
        )
    if not env.MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN or not secrets.compare_digest(
        x_dev_authorization,
        env.MLPA_EXPERIMENTATION_AUTHORIZATION_TOKEN,
    ):
        raise HTTPException(status_code=401, detail="Invalid x-dev-authorization")

    fxa_profile = await fxa_auth(authorization)
    if not fxa_profile or fxa_profile.get("error"):
        raise HTTPException(
            status_code=401,
            detail=fxa_profile.get("error", "Invalid FxA auth"),
        )
    return fxa_profile
