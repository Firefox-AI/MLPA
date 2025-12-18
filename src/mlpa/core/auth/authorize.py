from typing import Annotated

from fastapi import Header, HTTPException

from mlpa.core.classes import AuthorizedChatRequest, ChatRequest, ServiceType
from mlpa.core.config import env
from mlpa.core.prometheus_metrics import metrics
from mlpa.core.routers.appattest import app_attest_auth
from mlpa.core.routers.fxa import fxa_auth
from mlpa.core.utils import parse_app_attest_jwt


async def authorize_request(
    chat_request: ChatRequest,
    authorization: Annotated[str, Header()],
    service_type: Annotated[ServiceType, Header()],
    use_app_attest: Annotated[bool | None, Header()] = None,
    use_qa_certificates: Annotated[bool | None, Header()] = None,
) -> AuthorizedChatRequest:
    metrics.auth_request_count_total.inc()
    if not authorization:
        metrics.auth_error_count_total.labels(error=f"MalformedRequest")
        raise HTTPException(status_code=401, detail="Missing authorization header")
    if use_app_attest:
        assertionAuth = parse_app_attest_jwt(authorization, "assert")
        data = await app_attest_auth(assertionAuth, chat_request, use_qa_certificates)
        if data:
            if data.get("error"):
                metrics.auth_error_count_total.labels(error=f"AppattestUnauthorized")
                raise HTTPException(status_code=401, detail=data["error"])
            return AuthorizedChatRequest(
                user=f"{assertionAuth.key_id_b64}:{service_type.value}",  # "user" is key_id_b64 from app attest
                **chat_request.model_dump(exclude_unset=True),
            )
    else:
        # FxA authorization
        fxa_user_id = fxa_auth(authorization)
        if fxa_user_id:
            if fxa_user_id.get("error"):
                metrics.auth_error_count_total.labels(error=f"FxAUnauthorized")
                raise HTTPException(status_code=401, detail=fxa_user_id["error"])
            return AuthorizedChatRequest(
                user=f"{fxa_user_id['user']}:{service_type.value}",
                **chat_request.model_dump(exclude_unset=True),
            )
    metrics.auth_error_count_total.labels(error=f"Cancelled")
    raise HTTPException(
        status_code=401, detail="Please authenticate with App Attest or FxA."
    )
