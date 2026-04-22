import hashlib
from typing import Annotated

from fastapi import Header, HTTPException, Request

from mlpa.core.auth.dev_auth import auth_with_key
from mlpa.core.classes import (
    AuthorizedChatRequest,
    AuthorizedRequestContext,
    ChatRequest,
    ServiceType,
)
from mlpa.core.config import env
from mlpa.core.routers.appattest import app_attest_auth
from mlpa.core.routers.fxa import fxa_auth
from mlpa.core.utils import extract_user_from_play_integrity_jwt, parse_app_attest_jwt


def _resolve_purpose(service_type_value: str, purpose_header: str | None) -> str:
    """Validate purpose header and return value; raise HTTPException if invalid."""
    valid = env.valid_purposes_for_service_type(service_type_value)
    requires = env.service_type_requires_purpose(service_type_value)
    purpose = (purpose_header or "").strip()

    if purpose:
        if purpose not in valid:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Invalid header 'purpose' for service-type '{service_type_value}'. "
                    f"Valid values: {', '.join(valid)}"
                ),
            )
        return purpose

    # If enforcement is enabled, require the header for service types that
    # have a configured purpose allowlist.
    if requires and env.MLPA_REQUIRE_PURPOSE_HEADER:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Header 'purpose' is required for service-type '{service_type_value}'. "
                f"Valid values: {', '.join(valid)}"
            ),
        )

    # NOTE: missing purpose is allowed.
    return ""


async def authorize_request(
    request: Request,
    chat_request: ChatRequest,
    authorization: Annotated[str, Header()],
    service_type: Annotated[ServiceType, Header()],
    purpose: Annotated[str | None, Header()] = None,
    x_dev_authorization: Annotated[str | None, Header()] = None,
    use_app_attest: Annotated[bool | None, Header()] = None,
    use_qa_certificates: Annotated[bool | None, Header()] = None,
    use_play_integrity: Annotated[bool | None, Header()] = None,
) -> AuthorizedChatRequest:
    authorized_context = await authorize_request_context(
        request=request,
        authorization=authorization,
        service_type=service_type,
        purpose=purpose,
        x_dev_authorization=x_dev_authorization,
        use_app_attest=use_app_attest,
        use_qa_certificates=use_qa_certificates,
        use_play_integrity=use_play_integrity,
    )
    return AuthorizedChatRequest(
        **authorized_context.model_dump(),
        **chat_request.model_dump(exclude_unset=True),
    )


async def authorize_request_context(
    request: Request,
    authorization: str,
    service_type: ServiceType,
    purpose: str | None = None,
    x_dev_authorization: str | None = None,
    use_app_attest: bool | None = None,
    use_qa_certificates: bool | None = None,
    use_play_integrity: bool | None = None,
) -> AuthorizedRequestContext:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    purpose_value = _resolve_purpose(service_type.value, purpose)
    if use_app_attest:
        # Apple App Attest
        body_bytes = await request.body()
        assertionAuth = parse_app_attest_jwt(authorization, "assert")
        expected_hash = hashlib.sha256(body_bytes).digest()
        data = await app_attest_auth(
            assertionAuth, expected_hash, bool(use_qa_certificates)
        )
        if not data or data.get("error"):
            raise HTTPException(status_code=401, detail=data["error"])
        return AuthorizedRequestContext(
            user=f"{assertionAuth.key_id_b64}:{service_type.value}",  # "user" is key_id_b64 from app attest
            service_type=service_type.value,
            purpose=purpose_value,
        )
    elif use_play_integrity:
        # Google Play integrity
        play_user_id = extract_user_from_play_integrity_jwt(authorization)
        return AuthorizedRequestContext(
            user=f"{play_user_id}:{service_type.value}",
            service_type=service_type.value,
            purpose=purpose_value,
        )
    elif service_type.value.endswith("-dev"):
        if x_dev_authorization is None:
            raise HTTPException(
                status_code=401,
                detail="x-dev-authorization required for dev service types (ai-dev, memories-dev, mochi-dev)",
            )
        fxa_profile = await auth_with_key(x_dev_authorization, authorization)
        return AuthorizedRequestContext(
            user=f"{fxa_profile['user']}:{service_type.value}",
            service_type=service_type.value,
            purpose=purpose_value,
        )
    else:
        fxa_user_id = await fxa_auth(authorization)
        if not fxa_user_id or fxa_user_id.get("error"):
            raise HTTPException(status_code=401, detail=fxa_user_id["error"])
        return AuthorizedRequestContext(
            user=f"{fxa_user_id['user']}:{service_type.value}",
            service_type=service_type.value,
            purpose=purpose_value,
        )
