from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from fastapi import HTTPException

from mlpa.core.config import env
from mlpa.core.routers.guest.guest import (
    GUEST_TOKEN_HEADERS,
    GUEST_TOKEN_ISSUER,
    GUEST_TOKEN_PREFIX,
    issue_guest_token,
)
from mlpa.core.utils import extract_user_from_play_integrity_jwt
from tests.consts import SAMPLE_REQUEST, TEST_FXA_TOKEN


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


def test_guest_observability_metrics(mocked_client_integration, metrics_spy):
    mocked_client_integration.get(
        "/verify/v1/guest", headers={"accept": "application/json"}
    )
    mocked_client_integration.get("/verify/v1/guest", follow_redirects=False)
    mocked_client_integration.get("/verify/v1/guest", follow_redirects=False)
    assert metrics_spy.value("guest_tokens_issued_total", flow="json") == 1
    assert metrics_spy.value("guest_tokens_issued_total", flow="redirect") == 2


# Guest kill switch (AIPLAT-1450)


def _guest_completion(client, token):
    return client.post(
        "/v1/chat/completions",
        headers={"authorization": f"Bearer {token}", "service-type": "ai"},
        json=SAMPLE_REQUEST.model_dump(),
    )


def _fxa_completion(client):
    return client.post(
        "/v1/chat/completions",
        headers={"authorization": f"Bearer {TEST_FXA_TOKEN}", "service-type": "ai"},
        json=SAMPLE_REQUEST.model_dump(),
    )


def _flip(client, action, key=None):
    return client.post(
        f"/user/guests/{action}",
        headers={"master_key": key or f"Bearer {env.MASTER_KEY}"},
    )


def test_kill_switch_stops_guests_and_keeps_fxa(
    mocked_client_integration, mocker, fake_redis, metrics_spy
):
    mocker.patch.object(env, "MLPA_GUEST_VIRTUAL_KEY", "sk-guest")
    client = mocked_client_integration
    token = issue_guest_token()
    assert _guest_completion(client, token).status_code == 200

    response = _flip(client, "disable")
    assert response.status_code == 200
    assert response.json() == {"guests_enabled": False}
    assert fake_redis.values[env.GUEST_KILL_SWITCH_REDIS_KEY] == "0"

    assert client.get("/verify/v1/guest").status_code == 404
    response = _guest_completion(client, token)
    assert response.status_code == 401
    assert response.json()["detail"] == "Guest access is disabled"
    assert _fxa_completion(client).status_code == 200
    client.get("/metrics")
    assert metrics_spy.value("guest_enabled") == 0

    assert _flip(client, "enable").json() == {"guests_enabled": True}
    assert _guest_completion(client, token).status_code == 200
    client.get("/metrics")
    assert metrics_spy.value("guest_enabled") == 1


def test_kill_switch_needs_master_key(mocked_client_integration, fake_redis):
    response = _flip(mocked_client_integration, "disable", key="Bearer wrong")
    assert response.status_code == 401
    assert env.GUEST_KILL_SWITCH_REDIS_KEY not in fake_redis.values


def test_kill_switch_enable_cannot_override_env(mocked_client_integration, mocker):
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", False)
    assert _flip(mocked_client_integration, "enable").json() == {
        "guests_enabled": False
    }
