import os
import time
from collections.abc import Iterator

import httpx
import pytest

from tests.consts import TEST_FXA_TOKEN
from tests.fixtures import (  # noqa: F401
    mocked_client_integration,
    use_real_get_or_create_user,
)

FXA_PASSWORD = "123dev123dev123dev"
FXA_CLIENT_ID = "5882386c6d801776"
FXA_STAGE_ACCOUNT_SERVER_URL = "https://api-accounts.stage.mozaws.net/v1"
FXA_STAGE_OAUTH_SERVER_URL = "https://oauth.stage.mozaws.net"


@pytest.fixture
def smoke_is_remote() -> bool:
    return bool(os.environ.get("SMOKE_BASE_URL"))


@pytest.fixture
def smoke_client(request):
    base_url = os.environ.get("SMOKE_BASE_URL")
    if base_url:
        with httpx.Client(base_url=base_url.rstrip("/"), timeout=30.0) as client:
            yield client
        return

    yield request.getfixturevalue("mocked_client_integration")


def _fxa_urls(env_name: str) -> tuple[str, str]:
    match env_name:
        case "stage":
            return FXA_STAGE_ACCOUNT_SERVER_URL, FXA_STAGE_OAUTH_SERVER_URL
        case _:
            pytest.fail("SMOKE_FXA_ENV must be 'stage'.")


def _verification_code(message: dict) -> str | None:
    return message.get("headers", {}).get("x-verify-code")


@pytest.fixture(scope="session")
def smoke_fxa_token() -> Iterator[str]:
    configured_token = os.environ.get("SMOKE_FXA_TOKEN")
    if configured_token:
        yield configured_token
        return

    if not os.environ.get("SMOKE_BASE_URL"):
        yield TEST_FXA_TOKEN
        return

    from fxa.core import Client
    from fxa.tests.utils import TestEmailAccount
    from fxa.tools.bearer import get_bearer_token

    env_name = os.environ.get("SMOKE_FXA_ENV", "stage")
    account_server_url, oauth_server_url = _fxa_urls(env_name)
    client_id = os.environ.get("SMOKE_FXA_CLIENT_ID", FXA_CLIENT_ID)
    password = os.environ.get("SMOKE_FXA_PASSWORD", FXA_PASSWORD)
    scopes = os.environ.get("SMOKE_FXA_SCOPES", "profile").split()
    email_account = TestEmailAccount()
    client = Client(account_server_url)

    try:
        session = client.create_account(email_account.email, password)
        time.sleep(1)
        email_account.fetch()
        for message in email_account.messages:
            code = _verification_code(message)
            if code:
                session.verify_email_code(code)
                break

        session = client.login(email_account.email, password)
        if not session.verified:
            pytest.fail("Generated FxA smoke user is not verified.")

        token = get_bearer_token(
            email_account.email,
            password,
            scopes=scopes,
            client_id=client_id,
            account_server_url=account_server_url,
            oauth_server_url=oauth_server_url,
        )
        yield token
    finally:
        try:
            email_account.clear()
        except Exception:
            pass
        try:
            client.destroy_account(email_account.email, password)
        except Exception:
            pass
