from proxy.core.config import env


def test_health_liveness(mocked_client_integration, httpx_mock):
	liveness_response = mocked_client_integration.get("/health/liveness")
	assert liveness_response.status_code == 200
	assert liveness_response.json() == {"status": "alive"}


def test_health_readiness(mocked_client_integration, httpx_mock):
	httpx_mock.add_response(
		method="GET",
		url=f"{env.LITELLM_API_BASE}/health/readiness",
		status_code=200,
		json={
			"status": "connected",
			"pg_server_dbs": {"postgres": "connected", "app_attest": "connected"},
			"litellm": {
				"status": "connected",
				"db": "connected",
				"cache": None,
				"litellm_version": "1.77.3",
				"success_callbacks": [
					"sync_deployment_callback_on_success",
					"_PROXY_VirtualKeyModelMaxBudgetLimiter",
					"_ProxyDBLogger",
					"_PROXY_MaxBudgetLimiter",
					"_PROXY_MaxParallelRequestsHandler_v3",
					"_PROXY_CacheControlCheck",
					"_PROXY_LiteLLMManagedFiles",
					"ServiceLogging",
				],
				"use_aiohttp_transport": True,
				"last_updated": "2025-10-10T00:00:00",
			},
		},
	)

	readiness_response = mocked_client_integration.get("/health/readiness")
	assert readiness_response.status_code == 200
	assert readiness_response.json().get("status") == "connected"
	assert readiness_response.json().get("pg_server_dbs") is not None
	assert readiness_response.json().get("litellm") is not None


def test_metrics_endpoint(mocked_client_integration):
	response = mocked_client_integration.get("/metrics")
	assert response.status_code == 200
	assert "requests_total" in response.text
