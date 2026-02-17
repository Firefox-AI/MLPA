import hashlib
from typing import Annotated

from fastapi import Header, HTTPException, Request

from mlpa.core.classes import AuthorizedChatRequest, ChatRequest, ServiceType
from mlpa.core.config import env
from mlpa.core.routers.appattest import app_attest_auth
from mlpa.core.routers.fxa import fxa_auth
from mlpa.core.utils import extract_user_from_play_integrity_jwt, parse_app_attest_jwt


async def authorize_request(
    request: Request,
    chat_request: ChatRequest,
    authorization: Annotated[str, Header()],
    service_type: Annotated[ServiceType, Header()],
    use_app_attest: Annotated[bool | None, Header()] = None,
    use_qa_certificates: Annotated[bool | None, Header()] = None,
    use_play_integrity: Annotated[bool | None, Header()] = None,
) -> AuthorizedChatRequest:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")
    if use_app_attest:
        # Apple App Attest
        body_bytes = await request.body()
        assertionAuth = parse_app_attest_jwt(authorization, "assert")
        expected_hash = hashlib.sha256(body_bytes).digest()
        data = await app_attest_auth(assertionAuth, expected_hash, use_qa_certificates)
        if not data or data.get("error"):
            raise HTTPException(status_code=401, detail=data["error"])
        return AuthorizedChatRequest(
            user=f"{assertionAuth.key_id_b64}:{service_type.value}",  # "user" is key_id_b64 from app attest
            service_type=service_type.value,
            **chat_request.model_dump(exclude_unset=True),
        )
    elif use_play_integrity:
        # Google Play integrity
        play_user_id = extract_user_from_play_integrity_jwt(authorization)
        return AuthorizedChatRequest(
            user=f"{play_user_id}:{service_type.value}",
            service_type=service_type.value,
            **chat_request.model_dump(exclude_unset=True),
        )
    else:
        fxa_user_id = await fxa_auth(authorization)
        if not fxa_user_id or fxa_user_id.get("error"):
            raise HTTPException(status_code=401, detail=fxa_user_id["error"])
        return AuthorizedChatRequest(
            user=f"{fxa_user_id['user']}:{service_type.value}",
            service_type=service_type.value,
            **chat_request.model_dump(exclude_unset=True),
        )
