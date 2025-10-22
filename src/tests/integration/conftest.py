import pytest
from fastapi.testclient import TestClient

from proxy import run as main_app
from tests.mocks import (
	MockAppAttestPGService,
	MockFxAService,
	MockGatewayPGService,
	mock_get_completion,
	mock_get_or_create_user,
	mock_verify_assert,
)


@pytest.fixture
def mocked_client_integration(mocker):
	"""
	This fixture mocks the database services and provides a TestClient.
	"""
	mock_app_attest_pg = MockAppAttestPGService()
	mock_gateway_pg = MockGatewayPGService()
	mock_fxa_client = MockFxAService(
		"test-client-id", "test-client-secret", "https://test-fxa.com"
	)

	mocker.patch("proxy.run.app_attest_pg", mock_app_attest_pg)
	mocker.patch(
		"proxy.core.routers.appattest.appattest.app_attest_pg", mock_app_attest_pg
	)
	mocker.patch("proxy.core.routers.health.health.app_attest_pg", mock_app_attest_pg)
	mocker.patch(
		"proxy.core.routers.appattest.appattest.app_attest_pg", mock_app_attest_pg
	)
	mocker.patch("proxy.run.gateway_pg", mock_gateway_pg)
	mocker.patch("proxy.core.routers.health.health.gateway_pg", mock_gateway_pg)

	mocker.patch("proxy.core.routers.fxa.fxa.client", mock_fxa_client)

	mocker.patch(
		"proxy.core.routers.appattest.middleware.verify_assert",
		side_effect=mock_verify_assert,
	)

	mocker.patch(
		"proxy.run.get_or_create_user",
		lambda *args, **kwargs: mock_get_or_create_user(
			mock_gateway_pg, *args, **kwargs
		),
	)
	mocker.patch(
		"proxy.run.get_completion",
		side_effect=mock_get_completion,
	)

	with TestClient(main_app.app) as client:
		yield client
