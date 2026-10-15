from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi import HTTPException

from mlpa.core.config import env
from mlpa.core.routers.guest.guest import (
    GUEST_TOKEN_HEADERS,
    GUEST_TOKEN_ISSUER,
    GUEST_TOKEN_PREFIX,
)
from mlpa.core.utils import extract_user_from_play_integrity_jwt


@pytest.fixture(autouse=True)
def guest_enabled(mocker):
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", True)


def test_guest_token_disabled(mocked_client_integration, mocker):
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", False)
    response = mocked_client_integration.get(
        "/verify/v1/guest", params={"state": "s"}, follow_redirects=False
    )

    assert response.status_code == 404
    assert "location" not in response.headers


def test_issue_guest_token(mocked_client_integration):
    response = mocked_client_integration.get(
        "/verify/v1/guest", params={"state": "a b&c"}, follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    location = urlsplit(response.headers["location"])
    assert location.path == "/verify/v1/guest/success"
    assert location.query == ""
    data = {k: v[0] for k, v in parse_qs(location.fragment).items()}
    assert data["token_type"] == "Bearer"
    assert data["state"] == "a b&c"
    assert "expires_in" not in data

    token = data["access_token"]
    assert jwt.get_unverified_header(token) == {"alg": "HS256", **GUEST_TOKEN_HEADERS}
    assert token.startswith(GUEST_TOKEN_PREFIX)
    claims = jwt.decode(
        token,
        env.MLPA_GUEST_TOKEN_SIGNING_SECRET,
        algorithms=["HS256"],
        audience="mlpa",
        issuer=GUEST_TOKEN_ISSUER,
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


def test_guest_success_page(mocked_client_integration):
    response = mocked_client_integration.get("/verify/v1/guest/success")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"


def test_issue_guest_token_without_state(mocked_client_integration):
    response = mocked_client_integration.get("/verify/v1/guest", follow_redirects=False)

    assert response.status_code == 302
    data = parse_qs(urlsplit(response.headers["location"]).fragment)
    assert data["token_type"] == ["Bearer"]
    assert "access_token" in data
    assert "state" not in data


def test_issue_guest_token_json(mocked_client_integration):
    response = mocked_client_integration.get(
        "/verify/v1/guest",
        params={"state": "s"},
        headers={"Accept": "application/json"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    data = response.json()
    assert data.keys() == {"access_token", "token_type"}
    assert data["token_type"] == "Bearer"
    assert jwt.get_unverified_header(data["access_token"])["typ"] == "at+JWT"


@pytest.mark.parametrize("params", [{"state": ""}, {"state": "x" * 257}])
def test_issue_guest_token_rejects_bad_state(mocked_client_integration, params):
    response = mocked_client_integration.get(
        "/verify/v1/guest", params=params, follow_redirects=False
    )

    assert response.status_code == 422
