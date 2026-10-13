"""
Smoke suite: narrow, fast happy-path checks for auth + data-flow response
shape (pytest.mark.smoke), distinct from unit/component/e2e by *where* they
run rather than what they mock.

Without SMOKE_BASE_URL: `smoke_client` falls back to the mocked TestClient
(mocked_client_integration), same as component tests -- this is what runs
in CI on every PR.

With SMOKE_BASE_URL set: `smoke_client` hits that URL directly (a real
deployment, e.g. stage/prod), and `smoke_fxa_token` must come from
SMOKE_FXA_TOKEN, a prod FxA token, since every deployment verifies against
prod FxA. This mode is for a post-deploy sanity check, not pre-merge CI.
"""

import os

import httpx
import pytest

from tests.consts import TEST_FXA_TOKEN
from tests.fixtures import (  # noqa: F401
    mocked_client_integration,
    use_real_get_or_create_user,
)


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


@pytest.fixture(scope="session")
def smoke_fxa_token() -> str:
    configured_token = os.environ.get("SMOKE_FXA_TOKEN")
    if configured_token:
        return configured_token

    if not os.environ.get("SMOKE_BASE_URL"):
        return TEST_FXA_TOKEN

    pytest.fail(
        "SMOKE_FXA_TOKEN must be set to a prod FxA token when SMOKE_BASE_URL is set."
    )
