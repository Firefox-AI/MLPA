import jwt
import pytest
from fastapi import HTTPException

from mlpa.core.config import env
from mlpa.core.utils import extract_user_from_play_integrity_jwt


def test_issue_guest_token(mocked_client_integration):
    response = mocked_client_integration.get("/verify/guest")

    assert response.status_code == 200
    data = response.json()
    assert data["token_type"] == "Bearer"
    assert data["expires_in"] == env.MLPA_GUEST_TOKEN_TTL_SECONDS

    token = data["access_token"]
    assert jwt.get_unverified_header(token)["typ"] == "at+JWT"
    claims = jwt.decode(
        token,
        env.MLPA_GUEST_TOKEN_SIGNING_SECRET,
        algorithms=["HS256"],
        audience="mlpa",
    )
    assert set(claims) == {
        "iss",
        "aud",
        "client_id",
        "scope",
        "sub",
        "iat",
        "exp",
        "jti",
    }
    assert claims["exp"] - claims["iat"] == env.MLPA_GUEST_TOKEN_TTL_SECONDS

    with pytest.raises(HTTPException) as exc:
        extract_user_from_play_integrity_jwt(f"Bearer {token}")
    assert exc.value.status_code == 401
