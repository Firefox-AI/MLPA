import importlib.metadata

from mlpa.core.config import env


def test_health_liveness(mocked_client_integration, httpx_mock):
    liveness_response = mocked_client_integration.get("/health/liveness")
    assert liveness_response.status_code == 200
    assert liveness_response.json() == {"status": "alive"}


def test_health_readiness(mocked_client_integration, httpx_mock):
    mlpa_version = importlib.metadata.version("mlpa")
    httpx_mock.add_response(
        method="GET",
        url=f"{env.LITELLM_API_BASE}/health/readiness",
        status_code=200,
        json={
            "status": "healthy",
            "db": "connected",
        },
    )
    httpx_mock.add_response(
        method="GET",
        url=f"{env.LITELLM_API_BASE}/public/model_hub/info",
        status_code=200,
        json={"litellm_version": "1.84.4"},
        is_optional=True,
    )

    readiness_response = mocked_client_integration.get("/health/readiness")
    assert readiness_response.status_code == 200
    assert readiness_response.json().get("status") == "connected"
    assert readiness_response.json().get("mlpa_version") == mlpa_version
    assert readiness_response.json().get("pg_server_dbs") is not None
    assert readiness_response.json().get("litellm") == {
        "litellm_version": "1.84.4",
        "status": "healthy",
        "db": "connected",
    }


def test_metrics_endpoint(mocked_client_integration):
    response = mocked_client_integration.get("/metrics")
    assert response.status_code == 200
    assert "requests_total" in response.text
