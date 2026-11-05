import pytest

from mlpa.core.config import env
from mlpa.core.middleware import traffic_contract_enforcer
from mlpa.core.services.redis_service import TrafficContractDecision
from tests.consts import SAMPLE_CHAT_REQUEST, TEST_FXA_TOKEN


@pytest.mark.parametrize("limited_dimension", ["rpm", "tpm"])
@pytest.mark.parametrize("hard_deny", [False, True])
def test_chat_completion_traffic_contract_denial_respects_flag(
    mocked_client_integration,
    mocker,
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
                    allowed=limited_dimension != "rpm",
                    feature_count=10_000,
                    basket_count=10_000,
                    retry_after_seconds=42,
                    limited_by="feature",
                    mode="borrowed",
                    ratio_over=1.1,
                ),
                TrafficContractDecision(
                    allowed=limited_dimension != "tpm",
                    feature_count=0,
                    basket_count=0,
                    retry_after_seconds=17,
                ),
            )
        ),
    )
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )
    get_completion = mocker.patch(
        "mlpa.run.get_completion",
        mocker.AsyncMock(return_value={"id": "chatcmpl-test"}),
    )

    response = mocked_client_integration.post(
        "/v1/chat/completions",
        headers={
            "authorization": f"Bearer {TEST_FXA_TOKEN}",
            "service-type": "ai",
            "purpose": "chat",
        },
        json=SAMPLE_CHAT_REQUEST.model_dump(exclude_unset=True),
    )

    if hard_deny:
        assert response.status_code == 429
        assert response.json() == {"detail": {"error": 13}}
        assert response.headers["Retry-After"] == (
            "42" if limited_dimension == "rpm" else "17"
        )
        get_completion.assert_not_awaited()
    else:
        assert response.status_code == 200
        assert response.json() == {"id": "chatcmpl-test"}
        get_completion.assert_awaited_once()
