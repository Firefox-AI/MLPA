from unittest.mock import AsyncMock, patch

from mlpa.core.metrics import SEARCH_MODEL
from tests.consts import SAMPLE_REQUEST, TEST_FXA_TOKEN


def _country_count(metrics_spy, **labels):
    return metrics_spy.value("requests_by_country_total", **labels)


def test_chat_records_country(mocked_client_integration, metrics_spy):
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
            "purpose": "chat",
            "X-Geo-Country": "DE",
        },
        json=SAMPLE_REQUEST.model_dump(),
    )
    assert response.status_code == 200
    assert (
        _country_count(
            metrics_spy, service_type="ai", model="test-model", client_country="DE"
        )
        == 1.0
    )


def test_chat_missing_geo_header_is_unknown(mocked_client_integration, metrics_spy):
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
            "purpose": "chat",
        },
        json=SAMPLE_REQUEST.model_dump(),
    )
    assert response.status_code == 200
    assert (
        _country_count(
            metrics_spy, service_type="ai", model="test-model", client_country="unknown"
        )
        == 1.0
    )


def test_chat_spoofed_geo_header_is_clamped(mocked_client_integration, metrics_spy):
    mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
            "purpose": "chat",
            "X-Geo-Country": "ZZZ",
        },
        json=SAMPLE_REQUEST.model_dump(),
    )
    assert (
        _country_count(
            metrics_spy, service_type="ai", model="test-model", client_country="unknown"
        )
        == 1.0
    )
    assert (
        _country_count(
            metrics_spy, service_type="ai", model="test-model", client_country="ZZZ"
        )
        == 0.0
    )


def test_search_records_country_with_search_model(
    mocked_client_integration, metrics_spy
):
    with patch("mlpa.run.get_search", new=AsyncMock(return_value={"results": []})):
        mocked_client_integration.post(
            "/v1/search",
            headers={
                "authorization": f"Bearer {TEST_FXA_TOKEN}",
                "service-type": "search",
                "X-Geo-Country": "US",
            },
            json={"query": "hello", "max_results": 3},
        )
    assert (
        _country_count(
            metrics_spy,
            service_type="search",
            model=SEARCH_MODEL,
            client_country="US",
        )
        == 1.0
    )
