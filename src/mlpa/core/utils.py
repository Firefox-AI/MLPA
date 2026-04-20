import ast
import base64
import json
import time
from functools import lru_cache
from typing import Any, Literal, NoReturn, cast, overload

from fastapi import HTTPException
from fxa.oauth import Client
from jwtoxide import DecodingKey, ValidationOptions, decode, encode

from mlpa.core.classes import AssertionAuth, AttestationAuth
from mlpa.core.config import (
    ERROR_CODE_MAX_USERS_REACHED,
    LITELLM_MASTER_AUTH_HEADERS,
    env,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.logger import logger
from mlpa.core.pg_services.services import app_attest_pg, litellm_pg
from mlpa.core.prometheus_metrics import PrometheusResult, metrics


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
        db_user = await litellm_pg.get_user(user_id)

        if db_user is None:
            # Enforce managed service types user capacity with DB-backed admission control.
            if (
                env.MLPA_ENFORCE_SIGNIN_CAP
                and service_type in env.MLPA_CAPPED_SERVICE_TYPES
            ):
                (
                    admitted,
                    newly_claimed,
                ) = await app_attest_pg.admit_managed_base_identity(
                    base_identity=base_identity
                )
                if not admitted:
                    raise HTTPException(
                        status_code=403,
                        detail={"error": ERROR_CODE_MAX_USERS_REACHED},
                    )
                claimed_new_identity = newly_claimed

            await client.post(
                f"{env.LITELLM_API_BASE}/customer/new",
                json={"user_id": user_id, "budget_id": budget_id},
                headers=LITELLM_MASTER_AUTH_HEADERS,
            )

            created_user = await litellm_pg.get_user(user_id)
            if created_user is None:
                # Admission may have succeeded but LiteLLM user creation did not.
                # Release the reserved slot to avoid claim/cap drift.
                if claimed_new_identity:
                    await app_attest_pg.maybe_release_managed_base_identity_if_no_managed_users(
                        base_identity=base_identity
                    )
                raise HTTPException(
                    status_code=500,
                    detail={"error": "User creation failed after admission"},
                )

            return [created_user, True]

        return [db_user, False]
    except HTTPException:
        raise
    except Exception as e:
        if claimed_new_identity:
            try:
                await app_attest_pg.maybe_release_managed_base_identity_if_no_managed_users(
                    base_identity=base_identity
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


def is_litellm_upstream_rate_limit(error_text: str) -> bool:
    """Detect upstream LiteLLM throttling errors for retry."""
    if not error_text:
        return False
    try:
        error_json = json.loads(error_text)
        if (
            error_json.get("status") == "RESOURCE_EXHAUSTED"
            or error_json.get("type") == "throttling_error"
        ):
            return True
    except Exception:
        pass
    normalized = error_text.replace(" ", "").lower()
    return (
        "litellm.ratelimiterror" in normalized
        or '"status":"resource_exhausted"' in normalized
        or '"type":"throttling_error"' in normalized
    )


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
            cast(Any, DecodingKey.from_secret(b"")),
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
            appAuth = AttestationAuth.model_validate(value)
        elif type == "assert":
            appAuth = AssertionAuth.model_validate(value)
        else:
            raise HTTPException(status_code=400, detail="Invalid App Attest type")
    except Exception as e:
        logger.error(f"App {type} JWT decode error: {e}")
        raise HTTPException(status_code=401, detail=f"Invalid App {type}")
    return appAuth


GENERIC_UPSTREAM_ERROR = "Upstream service returned an error"


@overload
def raise_and_log(
    e: Exception,
    stream: Literal[True],
    response_code: int | None = None,
    response_text_prefix: str | None = None,
) -> bytes: ...


@overload
def raise_and_log(
    e: Exception,
    stream: Literal[False] = False,
    response_code: int | None = None,
    response_text_prefix: str | None = None,
) -> NoReturn: ...


def raise_and_log(
    e: Exception,
    stream: bool = False,
    response_code: int | None = None,
    response_text_prefix: str | None = None,
) -> bytes | NoReturn:
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
    start_time = time.perf_counter()
    token = authorization.removeprefix("Bearer ").split()[0]
    try:
        result = PrometheusResult.ERROR
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
        result = PrometheusResult.SUCCESS
        metrics.access_token_verifications_total.inc()
        return payload["sub"]
    except Exception as e:
        logger.error(f"Play Integrity JWT decode error: {e}")
        raise HTTPException(status_code=401, detail="Invalid MLPA access token")
    finally:
        metrics.validate_access_token_latency.labels(result=result).observe(
            time.perf_counter() - start_time
        )


def issue_mlpa_access_token(user_id: str) -> str:
    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + env.MLPA_ACCESS_TOKEN_TTL_SECONDS,
        "iss": "mlpa",
        "typ": "mlpa_access",
    }
    return encode(
        cast(Any, payload),
        env.MLPA_ACCESS_TOKEN_SECRET,
        algorithm="HS256",
    )
