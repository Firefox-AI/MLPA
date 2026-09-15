import os
import uuid

import asyncpg
import httpx
import pytest
import redis
from fastapi.testclient import TestClient

from mlpa import run as main_app
from mlpa.core.config import LITELLM_MASTER_AUTH_HEADERS, env
from tests.helpers import FxAStub
from tests.mocks import (
    MockAppAttestPGService,
    MockFxAClientForMockRouter,
    MockFxAService,
    MockLiteLLMPGService,
    mock_app_attest_auth,
    mock_get_completion,
    mock_get_or_create_user,
    mock_verify_assert,
    mock_verify_attest,
)

PROXY_TIMEOUT_S = 30.0


@pytest.fixture
def use_real_get_or_create_user():
    return False


@pytest.fixture
def mocked_client_integration(mocker, use_real_get_or_create_user):
    """
    This fixture mocks the database services and provides a TestClient.
    """
    mock_litellm_pg = MockLiteLLMPGService()
    mock_app_attest_pg = MockAppAttestPGService(mock_litellm_pg)
    mock_fxa_client = MockFxAService(
        "test-client-id", "test-client-secret", "https://test-fxa.com"
    )
    mock_fxa_client_for_mock_router = MockFxAClientForMockRouter(
        "test-client-id", "test-client-secret", "https://test-fxa.com"
    )

    mocker.patch("mlpa.run.app_attest_pg", mock_app_attest_pg)
    mocker.patch(
        "mlpa.core.routers.appattest.appattest.app_attest_pg", mock_app_attest_pg
    )
    mocker.patch("mlpa.core.routers.health.health.app_attest_pg", mock_app_attest_pg)
    mocker.patch("mlpa.core.routers.user.user.app_attest_pg", mock_app_attest_pg)
    mocker.patch("mlpa.core.utils.app_attest_pg", mock_app_attest_pg)
    mocker.patch("mlpa.run.litellm_pg", mock_litellm_pg)
    mocker.patch("mlpa.core.routers.health.health.litellm_pg", mock_litellm_pg)
    mocker.patch("mlpa.core.utils.litellm_pg", mock_litellm_pg)

    mocker.patch("mlpa.core.auth.fxa.client", mock_fxa_client)

    mocker.patch(
        "mlpa.core.routers.mock.mock.fxa_client", mock_fxa_client_for_mock_router
    )

    async def _mock_verify_attest(
        key_id_b64: str,
        challenge: str,
        attestation_obj: str,
        use_qa_certificates: bool,
        bundle_id: str,
    ):
        return await mock_verify_attest(
            mock_app_attest_pg,
            key_id_b64,
            challenge,
            attestation_obj,
            use_qa_certificates,
            bundle_id=bundle_id,
        )

    mocker.patch(
        "mlpa.core.routers.appattest.middleware.verify_attest",
        side_effect=_mock_verify_attest,
    )
    mocker.patch(
        "mlpa.core.routers.appattest.middleware.verify_assert",
        side_effect=mock_verify_assert,
    )
    mocker.patch(
        "mlpa.core.routers.appattest.middleware.app_attest_auth",
        side_effect=mock_app_attest_auth,
    )

    mocker.patch(
        "mlpa.run.get_completion",
        side_effect=mock_get_completion,
    )

    if not use_real_get_or_create_user:
        mocker.patch(
            "mlpa.run.get_or_create_user_for_completion",
            lambda user_id, req: mock_get_or_create_user(
                mock_litellm_pg, mock_app_attest_pg, user_id
            ),
        )
        mocker.patch(
            "mlpa.core.routers.mock.mock.get_or_create_user_for_completion",
            lambda user_id, req: mock_get_or_create_user(
                mock_litellm_pg, mock_app_attest_pg, user_id
            ),
        )
        mocker.patch(
            "mlpa.core.routers.mock.mock.get_or_create_user",
            lambda *args, **kwargs: mock_get_or_create_user(
                mock_litellm_pg, mock_app_attest_pg, *args, **kwargs
            ),
        )
    with TestClient(main_app.app) as client:
        yield client


@pytest.fixture
def proxy():
    """Client for talking to LiteLLM directly, pre-loaded with the master
    key. Only for reading back what the proxy recorded and for its admin
    endpoints -- anything MLPA can do itself belongs on real_backend_client
    so the test covers the integration rather than the proxy alone."""
    with httpx.Client(
        base_url=env.LITELLM_API_BASE,
        headers=LITELLM_MASTER_AUTH_HEADERS,
        timeout=PROXY_TIMEOUT_S,
    ) as client:
        yield client


@pytest.fixture
async def litellm_db():
    """Direct connection to LiteLLM's own Postgres database. MLPA writes
    into this schema with hand-written SQL (litellm_pg_service)"""
    conn = await asyncpg.connect(
        f"{env.PG_DB_URL.rstrip('/')}/{env.LITELLM_DB_NAME}",
        timeout=PROXY_TIMEOUT_S,
    )
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture
def redis_client():
    """LiteLLM's Redis, as exposed by litellm_docker_compose.yaml. MLPA
    doesn't talk to it, but its per-user budgets are only enforced across
    replicas"""
    client = redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", "6379")),
        decode_responses=True,
    )
    try:
        yield client
    finally:
        client.close()


@pytest.fixture
def real_backend_client():
    """
    TestClient wired to the real LiteLLM proxy + Postgres, plus a bearer
    token/user_id pair unique to this test. Only FxA verification is
    mocked -- budget/rate-limit enforcement is a backend concern unrelated
    to auth, and this way the test doesn't depend on a live FxA account.

    Yields (client, token, base_identity).
    """
    from unittest.mock import patch

    from fastapi.testclient import TestClient

    from mlpa import run as main_app

    token = f"e2e-token-{uuid.uuid4().hex}"
    base_identity = f"e2e-{uuid.uuid4().hex[:12]}"
    fxa_stub = FxAStub(token, base_identity)
    with patch("mlpa.core.auth.fxa.client", fxa_stub):
        with TestClient(main_app.app) as client:
            yield client, token, base_identity
