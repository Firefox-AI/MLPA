import ast
import base64
import json
import time

from fastapi import HTTPException
from fxa.oauth import Client
from jwtoxide import DecodingKey, ValidationOptions, decode, encode

from mlpa.core.classes import AssertionAuth, AttestationAuth
from mlpa.core.config import LITELLM_MASTER_AUTH_HEADERS, env
from mlpa.core.http_client import get_http_client
from mlpa.core.logger import logger


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

    client = get_http_client()
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


GENERIC_UPSTREAM_ERROR = "Upstream service returned an error"


def raise_and_log(
    e: Exception,
    stream: bool = False,
    response_code: int | None = None,
    response_text_prefix: str | None = None,
):
    """
    Log an upstream exception and return or raise a standardized FastAPI response.

    When streaming, returns an SSE payload as bytes. Otherwise, raises an
    HTTPException with the chosen status code and a sanitized error message.
    If the upstream error body contains a nested error message, it is extracted
    so clients receive the actual upstream detail in debug mode. (dev environment only)
    """
    response = getattr(e, "response", None)
    error_text = response.text if response is not None else ""
    detail_text = error_text or str(e)
    if error_text:
        try:
            error_payload = json.loads(error_text)
            message = error_payload.get("error", {}).get("message")
            if isinstance(message, str) and message.startswith("{'error':"):
                try:
                    message_obj = ast.literal_eval(message)
                    message = message_obj.get("error", message)
                except (ValueError, SyntaxError):
                    pass
            if isinstance(message, str) and message:
                detail_text = message
        except (json.JSONDecodeError, AttributeError, TypeError):
            pass
    status_code = response_code or getattr(response, "status_code", None) or 500
    logger.error(f"{response_text_prefix or GENERIC_UPSTREAM_ERROR}: {detail_text}")
    if stream:
        error_msg = detail_text if env.MLPA_DEBUG else GENERIC_UPSTREAM_ERROR
        payload = {"code": status_code, "error": error_msg}
        return f"data: {json.dumps(payload)}\n\n".encode()
    else:
        raise HTTPException(
            status_code=status_code,
            detail={
                "error": detail_text
                if env.MLPA_DEBUG
                else response_text_prefix or GENERIC_UPSTREAM_ERROR
            },
        )


def extract_user_from_play_integrity_jwt(authorization: str):
    token = authorization.removeprefix("Bearer ").split()[0]
    try:
        payload = decode(
            token,
            env.MLPA_ACCESS_TOKEN_SECRET,
            ValidationOptions(
                required_spec_claims={"exp", "iat", "sub"},
                iss={"mlpa"},
                aud=None,
                validate_aud=False,
                validate_exp=True,
                validate_nbf=False,
                verify_signature=True,
                algorithms=["HS256"],
            ),
        )
        return payload["sub"]
    except Exception as e:
        logger.error(f"Play Integrity JWT decode error: {e}")
        raise HTTPException(status_code=401, detail="Invalid MLPA access token")


def issue_mlpa_access_token(user_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + env.MLPA_ACCESS_TOKEN_TTL_SECONDS,
        "iss": "mlpa",
        "typ": "mlpa_access",
    }
    return encode(payload, env.MLPA_ACCESS_TOKEN_SECRET, algorithm="HS256")
