import base64

import httpx
from fastapi import HTTPException
from fxa.oauth import Client
from jwtoxide import DecodingKey, ValidationOptions, decode
from loguru import logger

from mlpa.core.classes import AssertionAuth, AttestationAuth
from mlpa.core.config import LITELLM_MASTER_AUTH_HEADERS, env


async def get_or_create_user(user_id: str):
    """Returns user info from LiteLLM, creating the user if they don't exist.
    Args:
        user_id (str): The user ID to look up or create. Format: "user_id:service_type" (e.g., "user123:ai")
    Returns:
        [user_info: dict, was_created: bool]
    """
    service_type = user_id.split(":")[1]

    # Get the appropriate budget_id from config based on service_type
    user_feature_budgets = env.user_feature_budget
    budget_id = user_feature_budgets[service_type]["budget_id"]

    async with httpx.AsyncClient() as client:
        try:
            params = {"end_user_id": user_id}
            response = await client.get(
                f"{env.LITELLM_API_BASE}/customer/info",
                params=params,
                headers=LITELLM_MASTER_AUTH_HEADERS,
            )
            user = response.json()

            if not user.get("user_id"):
                await client.post(
                    f"{env.LITELLM_API_BASE}/customer/new",
                    json={"user_id": user_id, "budget_id": budget_id},
                    headers=LITELLM_MASTER_AUTH_HEADERS,
                )
                response = await client.get(
                    f"{env.LITELLM_API_BASE}/customer/info",
                    params=params,
                    headers=LITELLM_MASTER_AUTH_HEADERS,
                )
                return [response.json(), True]
            return [user, False]
        except Exception as e:
            logger.error(f"Error fetching or creating user {user_id}: {e}")
            raise HTTPException(
                status_code=500, detail={"error": f"Error fetching user info"}
            )


def b64decode_safe(data_b64: str, obj_name: str = "object") -> bytes:
    try:
        return base64.urlsafe_b64decode(data_b64)
    except Exception as e:
        logger.error(f"Error decoding base64 for {obj_name}: {e}")
        raise HTTPException(status_code=400, detail={obj_name: f"Invalid Base64"})


def get_fxa_client():
    fxa_url = (
        "https://api-accounts.stage.mozaws.net/v1"
        if env.MLPA_DEBUG
        else "https://oauth.accounts.firefox.com/v1"
    )
    return Client(env.CLIENT_ID, env.CLIENT_SECRET, fxa_url)


def is_rate_limit_error(error_response: dict, keywords: list[str]) -> bool:
    """Check if the error response indicates a budget or rate limit exceeded error."""
    error = error_response.get("error", {})
    error_text = f"{error.get('type', '')} {error.get('message', '')}".lower()
    return any(indicator in error_text for indicator in keywords)


def parse_app_attest_jwt(authorization: str, type: str):
    # Parse App Attest/Assert authorization JWT
    try:
        # Remove "Bearer " prefix if present
        token = authorization.removeprefix("Bearer ").strip()
        value = decode(
            token,
            DecodingKey.from_secret(b""),
            ValidationOptions(
                required_spec_claims={"iat"},
                aud=None,
                iss=None,
                # Validation is not necessary here since we only need to parse the payload
                # Authorization is done later in the attestation/assertion verification process
                validate_aud=False,
                validate_exp=False,
                validate_nbf=False,
                verify_signature=False,
            ),
        )
        if type == "attest":
            appAuth = AttestationAuth(**value)
        elif type == "assert":
            appAuth = AssertionAuth(**value)
        else:
            raise HTTPException(status_code=400, detail="Invalid App Attest type")
    except Exception as e:
        logger.error(f"App {type} JWT decode error: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid App {type}")
    return appAuth
