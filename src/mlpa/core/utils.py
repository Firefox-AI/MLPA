import ast
import base64
import json
import time
from functools import lru_cache

import httpx
from fastapi import HTTPException
from fxa.oauth import Client
from jwtoxide import DecodingKey, ValidationOptions, decode, encode
from tenacity import retry, stop_after_attempt, wait_exponential

from mlpa.core.classes import AssertionAuth, AttestationAuth
from mlpa.core.config import (
    ERROR_CODE_MAX_USERS_REACHED,
    LITELLM_MASTER_AUTH_HEADERS,
    env,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.logger import logger
from mlpa.core.pg_services.services import litellm_pg


def should_retry_on_litellm_error(exception: Exception) -> bool:
    if isinstance(exception, httpx.HTTPStatusError):
        status_code = exception.response.status_code
        if status_code == 429:
            return is_litellm_upstream_rate_limit(exception.response.text)
        return status_code in {502, 503, 504}
    return isinstance(
        exception,
        (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
        ),
    )


def is_litellm_upstream_rate_limit(error_text: str) -> bool:
    """Detect upstream LiteLLM throttling errors for retry."""
    if not error_text:
        return False
    # Try to parse as JSON and check fields
    try:
        error_json = json.loads(error_text)
        if (
            error_json.get("status") == "RESOURCE_EXHAUSTED"
            or error_json.get("type") == "throttling_error"
        ):
            return True
    except Exception:
        pass
    # Fallback to normalized string matching
    normalized = error_text.replace(" ", "").lower()
    return (
        "litellm.ratelimiterror" in normalized
        or '"status":"resource_exhausted"' in normalized
        or '"type":"throttling_error"' in normalized
    )


def log_litellm_retry_attempt(retry_state) -> None:
    exception = retry_state.outcome.exception()
    next_wait = getattr(retry_state.next_action, "sleep", "?")
    if isinstance(exception, httpx.HTTPStatusError):
        logger.warning(
            f"Retrying LiteLLM request: attempt {retry_state.attempt_number}, "
            f"status_code={exception.response.status_code}, next wait {next_wait}s"
        )
    else:
        logger.warning(
            f"Retrying LiteLLM request: attempt {retry_state.attempt_number}, "
            f"error_type={type(exception).__name__}, next wait {next_wait}s"
        )


async def litellm_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    params: dict | None = None,
    json: dict | None = None,
    timeout: float | None = None,
    stream: bool = False,
):
    if stream:
        request = client.build_request(method, url, headers=headers, json=json)
        if timeout is not None:
            request.extensions["timeout"] = {
                "connect": timeout,
                "read": timeout,
                "write": timeout,
                "pool": timeout,
            }
        response = await client.send(request, stream=True)
    else:
        response = await client.request(
            method, url, headers=headers, params=params, json=json, timeout=timeout
        )

    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int) and status_code >= 400:
        try:
            await response.aread()
        except (AttributeError, TypeError):
            pass
        if stream:
            await response.aclose()
        response.raise_for_status()
    return response


@retry(
    wait=wait_exponential(multiplier=1, min=1, max=4),
    stop=stop_after_attempt(5),
    retry=lambda state: (
        should_retry_on_litellm_error(state.outcome.exception())
        if state.outcome.failed
        else False
    ),
    before_sleep=log_litellm_retry_attempt,
    reraise=True,
)
async def litellm_request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    params: dict | None = None,
    json: dict | None = None,
    timeout: float | None = None,
    stream: bool = False,
):
    return await litellm_request(
        client,
        method,
        url,
        headers,
        params=params,
        json=json,
        timeout=timeout,
        stream=stream,
    )


async def get_or_create_user(user_id: str):
    """Returns user info from LiteLLM, creating the user if they don't exist.
    Args:
        user_id (str): The user ID to look up or create. Format: "user_id:service_type" (e.g., "user123:ai")
    Returns:
        [user_info: dict, was_created: bool]
    """
    base_identity, _sep, service_type = user_id.partition(":")
    if not service_type:
        raise HTTPException(status_code=400, detail={"error": "Invalid user_id format"})

    # Get the appropriate budget_id from config based on service_type
    user_feature_budgets = env.user_feature_budget
    budget_id = user_feature_budgets[service_type]["budget_id"]

    client = get_http_client()
    claimed_new_identity = False
    try:
        params = {"end_user_id": user_id}

        response = await litellm_request_with_retry(
            client,
            "GET",
            f"{env.LITELLM_API_BASE}/customer/info",
            headers=LITELLM_MASTER_AUTH_HEADERS,
            params=params,
        )
        user = response.json()

        if not user.get("user_id"):
            # Enforce managed service types user capacity with DB-backed admission control.
            if (
                env.MLPA_ENFORCE_SIGNIN_CAP
                and service_type in env.MLPA_CAPPED_SERVICE_TYPES
            ):
                admitted, newly_claimed = await litellm_pg.admit_managed_base_identity(
                    base_identity=base_identity
                )
                if not admitted:
                    raise HTTPException(
                        status_code=403,
                        detail={"error": ERROR_CODE_MAX_USERS_REACHED},
                    )
                claimed_new_identity = newly_claimed

            await litellm_request_with_retry(
                client,
                "POST",
                f"{env.LITELLM_API_BASE}/customer/new",
                headers=LITELLM_MASTER_AUTH_HEADERS,
                json={"user_id": user_id, "budget_id": budget_id},
            )
            response = await litellm_request_with_retry(
                client,
                "GET",
                f"{env.LITELLM_API_BASE}/customer/info",
                headers=LITELLM_MASTER_AUTH_HEADERS,
                params=params,
            )

            created_user = response.json()
            if not created_user.get("user_id"):
                # Admission may have succeeded but LiteLLM user creation did not.
                # Release the reserved slot to avoid claim/cap drift.
                if claimed_new_identity:
                    await litellm_pg.maybe_release_managed_base_identity_if_no_managed_users(
                        base_identity=base_identity
                    )
                raise HTTPException(
                    status_code=500,
                    detail={"error": "User creation failed after admission"},
                )

            return [created_user, True]
        return [user, False]
    except HTTPException:
        raise
    except Exception as e:
        if claimed_new_identity:
            try:
                await (
                    litellm_pg.maybe_release_managed_base_identity_if_no_managed_users(
                        base_identity=base_identity
                    )
                )
            except Exception as release_e:
                logger.error(
                    f"Failed releasing managed capacity claim for base_identity={base_identity}: {release_e}"
                )
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


@lru_cache(maxsize=1)
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


def is_context_window_error(error_text: str) -> bool:
    """Check if the error indicates context window exceeded (LiteLLM/providers)."""
    if not error_text:
        return False
    text = error_text.lower()
    indicators = [
        "contextwindowexceeded",
        "maximum context length",
        "context window exceeded",
        "context length",
    ]
    return any(ind in text for ind in indicators)


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
