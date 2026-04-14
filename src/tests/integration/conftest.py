import pytest
from fastapi.testclient import TestClient

from mlpa import run as main_app
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

    mocker.patch("mlpa.core.routers.fxa.fxa.client", mock_fxa_client)

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
