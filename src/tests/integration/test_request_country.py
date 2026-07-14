from unittest.mock import AsyncMock, patch

from mlpa.core.metrics import SEARCH_MODEL
from mlpa.core.utils import clamp_model
from tests.consts import SAMPLE_REQUEST, TEST_FXA_TOKEN


def _country_count(metrics_spy, **labels):
    return metrics_spy.value("requests_by_country_total", **labels)


def _chat_payload(model: str = "openai/gpt-4o"):
    return SAMPLE_REQUEST.model_copy(update={"model": model}).model_dump()


def test_chat_records_country(mocked_client_integration, metrics_spy):
    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
            "purpose": "chat",
            "X-Geo-Country": "DE",
        },
        json=_chat_payload(),
    )
    assert response.status_code == 200
    assert (
        _country_count(
            metrics_spy,
            service_type="ai",
            model="openai/gpt-4o",
            client_country="DE",
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
        json=_chat_payload(),
    )
    assert response.status_code == 200
    assert (
        _country_count(
            metrics_spy,
            service_type="ai",
            model="openai/gpt-4o",
            client_country="unknown",
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
        json=_chat_payload(),
    )
    assert (
        _country_count(
            metrics_spy,
            service_type="ai",
            model="openai/gpt-4o",
            client_country="unknown",
        )
        == 1.0
    )
    assert (
        _country_count(
            metrics_spy,
            service_type="ai",
            model="openai/gpt-4o",
            client_country="ZZZ",
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


def test_chat_unknown_model_country_uses_invalid_model_bucket(
    mocked_client_integration, metrics_spy
):
    mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
            "purpose": "chat",
            "X-Geo-Country": "CA",
        },
        json=_chat_payload("not-a-configured-model"),
    )

    assert clamp_model("not-a-configured-model") == "invalid"
    assert (
        _country_count(
            metrics_spy,
            service_type="ai",
            model="invalid",
            client_country="CA",
        )
        == 1.0
    )
