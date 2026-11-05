import pytest
from pytest_httpx import IteratorStream

from mlpa.core.completions import get_completion as proxy_completion
from mlpa.core.config import LITELLM_COMPLETIONS_URL, LITELLM_SEARCH_URL, env
from mlpa.core.middleware import traffic_contract_enforcer
from mlpa.core.services.redis_service import TrafficContractDecision
from tests.consts import SAMPLE_CHAT_REQUEST, TEST_FXA_TOKEN


@pytest.mark.parametrize("endpoint", ["chat", "stream", "search"])
@pytest.mark.parametrize("limited_dimension", ["rpm", "tpm", "both"])
@pytest.mark.parametrize("hard_deny", [False, True])
def test_traffic_contract_denial_records_availability_once(
    mocked_client_integration,
    mocker,
    httpx_mock,
    metrics_spy,
    endpoint,
    limited_dimension,
    hard_deny,
):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    mocker.patch.object(env, "TRAFFIC_CONTRACT_429_IF_NOT_ALLOWED", hard_deny)
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_feature_traffic_contracts",
        mocker.AsyncMock(
            return_value=(
                TrafficContractDecision(
                    allowed=limited_dimension == "tpm",
                    feature_count=10_000,
                    basket_count=10_000,
                    retry_after_seconds=42,
                    limited_by="feature",
                    mode="borrowed",
                    ratio_over=1.1,
                ),
                TrafficContractDecision(
                    allowed=limited_dimension == "rpm",
                    feature_count=10_000,
                    basket_count=10_000,
                    retry_after_seconds=17,
                    limited_by="basket",
                    mode="degraded",
                    ratio_over=1.2,
                ),
            )
        ),
    )
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )
    # Use the actual completion handler so continuation records its own outcome.
    mocker.patch("mlpa.run.get_completion", side_effect=proxy_completion)
    service_type = "search" if endpoint == "search" else "ai"
    if not hard_deny:
        if endpoint == "search":
            httpx_mock.add_response(
                method="POST",
                url=f"{LITELLM_SEARCH_URL}/exa-search",
                json={"results": []},
            )
        elif endpoint == "stream":
            httpx_mock.add_response(
                method="POST",
                url=LITELLM_COMPLETIONS_URL,
                stream=IteratorStream(
                    [
                        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                        b"data: [DONE]\n\n",
                    ]
                ),
            )
        else:
            httpx_mock.add_response(
                method="POST",
                url=LITELLM_COMPLETIONS_URL,
                json={"choices": [{"message": {"role": "assistant", "content": "ok"}}]},
            )
    body = (
        {"query": "weather", "max_results": 3}
        if endpoint == "search"
        else {
            **SAMPLE_CHAT_REQUEST.model_dump(exclude_unset=True),
            "stream": endpoint == "stream",
        }
    )
    response = mocked_client_integration.post(
        "/v1/search" if endpoint == "search" else "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": service_type,
            "purpose": "" if endpoint == "search" else "chat",
            "X-Geo-Country": "US",
        },
        json=body,
    )
    if hard_deny:
        assert response.status_code == 429
        assert response.json() == {"detail": {"error": 13}}
        assert response.headers["Retry-After"] == (
            "17" if limited_dimension == "tpm" else "42"
        )
        assert not httpx_mock.get_requests()
    else:
        assert response.status_code == 200
        assert len(httpx_mock.get_requests()) == 1

    metric = "search_availability" if endpoint == "search" else "chat_availability"
    samples = [s for s in metrics_spy.samples(metric) if s.name.endswith("_total")]
    assert len(samples) == 1
    assert samples[0].value == 1
    assert samples[0].labels["reason"] == (
        "traffic_contract_violation" if hard_deny else "valid_response"
    )
    assert samples[0].labels["outcome"] == ("excluded" if hard_deny else "success")
    assert samples[0].labels["service_type"] == service_type
    country_samples = [
        s
        for s in metrics_spy.samples(f"{metric}_by_country")
        if s.name.endswith("_total")
    ]
    assert len(country_samples) == 1
    assert country_samples[0].value == 1
    assert country_samples[0].labels["client_country"] == "US"
    assert country_samples[0].labels["outcome"] == samples[0].labels["outcome"]
    other_metric = (
        "chat_availability" if endpoint == "search" else "search_availability"
    )
    assert not metrics_spy.samples(other_metric)
