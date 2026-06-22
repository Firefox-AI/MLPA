import importlib.metadata
from unittest.mock import AsyncMock

import httpx

from mlpa.core import migrations
from mlpa.core.config import env

READINESS_URL = f"{env.LITELLM_API_BASE}/health/readiness"
INFO_URL = f"{env.LITELLM_API_BASE}/public/model_hub/info"


def _mock_litellm_ready(httpx_mock, status="healthy", db="connected", status_code=200):
    httpx_mock.add_response(
        method="GET",
        url=READINESS_URL,
        status_code=status_code,
        json={"status": status, "db": db},
    )
    httpx_mock.add_response(
        method="GET",
        url=INFO_URL,
        status_code=200,
        json={"litellm_version": "1.84.4"},
        is_optional=True,
    )


def test_health_liveness(mocked_client_integration, httpx_mock):
    liveness_response = mocked_client_integration.get("/health/liveness")
    assert liveness_response.status_code == 200
    assert liveness_response.json() == {"status": "alive"}


def test_readiness_200_when_all_healthy(mocked_client_integration, httpx_mock, mocker):
    mlpa_version = importlib.metadata.version("mlpa")
    _mock_litellm_ready(httpx_mock)
    # Pin both sides to a fixed sentinel so the match is the handler's doing, not
    # an artifact of the mock deriving `current` from expected_heads() itself.
    fixed = frozenset({"sentinel_head_abc123"})
    mocker.patch("mlpa.core.routers.health.health.expected_heads", return_value=fixed)
    mocker.patch(
        "mlpa.core.routers.health.health.app_attest_pg.current_revisions",
        AsyncMock(return_value=set(fixed)),
    )

    response = mocked_client_integration.get("/health/readiness")
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "connected"
    assert body["mlpa_version"] == mlpa_version
    assert body["pg_server_dbs"] == {"postgres": "connected", "app_attest": "connected"}
    assert body["migration"] == {
        "expected": ["sentinel_head_abc123"],
        "current": ["sentinel_head_abc123"],
    }
    assert body["litellm"]["db"] == "connected"
    assert body["litellm"]["litellm_version"] == "1.84.4"


def test_readiness_503_when_litellm_pool_down(
    mocked_client_integration, httpx_mock, mocker
):
    _mock_litellm_ready(httpx_mock)
    mocker.patch(
        "mlpa.core.routers.health.health.litellm_pg.ping",
        AsyncMock(return_value=False),
    )

    response = mocked_client_integration.get("/health/readiness")
    body = response.json()

    assert response.status_code == 503
    assert body["status"] == "degraded"
    assert body["pg_server_dbs"]["postgres"] == "offline"


def test_readiness_503_when_app_attest_pool_down(
    mocked_client_integration, httpx_mock, mocker
):
    _mock_litellm_ready(httpx_mock)
    mocker.patch(
        "mlpa.core.routers.health.health.app_attest_pg.current_revisions",
        AsyncMock(side_effect=OSError("connection refused")),
    )

    response = mocked_client_integration.get("/health/readiness")
    body = response.json()

    assert response.status_code == 503
    assert body["pg_server_dbs"]["app_attest"] == "offline"


def test_readiness_503_when_migration_behind(
    mocked_client_integration, httpx_mock, mocker
):
    _mock_litellm_ready(httpx_mock)
    mocker.patch(
        "mlpa.core.routers.health.health.app_attest_pg.current_revisions",
        AsyncMock(return_value={"5b4ed32c7b2b"}),
    )

    response = mocked_client_integration.get("/health/readiness")
    body = response.json()

    assert response.status_code == 503
    # Pool is reachable; only the schema is behind.
    assert body["pg_server_dbs"]["app_attest"] == "connected"
    assert body["migration"]["current"] == ["5b4ed32c7b2b"]
    assert body["migration"]["expected"] == sorted(migrations.expected_heads())


def test_readiness_503_when_database_has_unknown_head(
    mocked_client_integration, httpx_mock, mocker
):
    _mock_litellm_ready(httpx_mock)
    mocker.patch(
        "mlpa.core.routers.health.health.app_attest_pg.current_revisions",
        AsyncMock(return_value={"deadbeefcafe"}),
    )

    response = mocked_client_integration.get("/health/readiness")
    body = response.json()

    assert response.status_code == 503
    assert body["pg_server_dbs"]["app_attest"] == "connected"
    assert body["migration"]["current"] == ["deadbeefcafe"]


def test_readiness_503_when_alembic_table_absent(
    mocked_client_integration, httpx_mock, mocker
):
    _mock_litellm_ready(httpx_mock)
    mocker.patch(
        "mlpa.core.routers.health.health.app_attest_pg.current_revisions",
        AsyncMock(return_value=set()),
    )

    response = mocked_client_integration.get("/health/readiness")
    body = response.json()

    assert response.status_code == 503
    # Table absent is distinct from pool-down: pool still connected, current == [].
    assert body["pg_server_dbs"]["app_attest"] == "connected"
    assert body["migration"]["current"] == []


def test_readiness_503_when_litellm_non_200(mocked_client_integration, httpx_mock):
    _mock_litellm_ready(httpx_mock, status_code=503)

    response = mocked_client_integration.get("/health/readiness")
    body = response.json()

    assert response.status_code == 503
    assert body["litellm"]["status"] == "unreachable"


def test_readiness_503_when_litellm_db_not_connected(
    mocked_client_integration, httpx_mock
):
    _mock_litellm_ready(httpx_mock, status="healthy", db="disconnected")

    response = mocked_client_integration.get("/health/readiness")

    assert response.status_code == 503


def test_readiness_503_when_litellm_times_out(mocked_client_integration, httpx_mock):
    httpx_mock.add_exception(
        httpx.ReadTimeout("timed out"), method="GET", url=READINESS_URL
    )
    httpx_mock.add_response(
        method="GET",
        url=INFO_URL,
        status_code=200,
        json={"litellm_version": "1.84.4"},
        is_optional=True,
    )

    response = mocked_client_integration.get("/health/readiness")
    body = response.json()

    assert response.status_code == 503
    assert body["litellm"]["status"] == "unreachable"


def test_readiness_503_when_heads_unresolvable(
    mocked_client_integration, httpx_mock, mocker
):
    _mock_litellm_ready(httpx_mock)
    mocker.patch(
        "mlpa.core.routers.health.health.expected_heads",
        side_effect=RuntimeError("cannot resolve"),
    )

    response = mocked_client_integration.get("/health/readiness")

    assert response.status_code == 503


def test_liveness_200_under_degraded_dep(mocked_client_integration, httpx_mock, mocker):
    mocker.patch(
        "mlpa.core.routers.health.health.litellm_pg.ping",
        AsyncMock(return_value=False),
    )

    response = mocked_client_integration.get("/health/liveness")

    assert response.status_code == 200
    assert response.json() == {"status": "alive"}


def test_metrics_endpoint(mocked_client_integration):
    response = mocked_client_integration.get("/metrics")
    assert response.status_code == 200
    assert "requests_total" in response.text
