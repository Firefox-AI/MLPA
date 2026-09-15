from mlpa.core.config import env
from mlpa.core.middleware import traffic_contract_enforcer
from mlpa.core.services.redis_service import TrafficContractDecision
from tests.consts import SAMPLE_CHAT_REQUEST, TEST_FXA_TOKEN


def test_chat_completion_continues_when_traffic_contract_exceeded(
    mocked_client_integration,
    mocker,
):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    mocker.patch.object(
        traffic_contract_enforcer.redis_service,
        "check_feature_traffic_contract",
        mocker.AsyncMock(
            return_value=TrafficContractDecision(
                allowed=False,
                feature_count=10_000,
                basket_count=10_000,
                retry_after_seconds=42,
                limited_by="feature",
                mode="borrowed",
                ratio_over=1.1,
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

    assert response.status_code == 200
    assert response.json() == {"id": "chatcmpl-test"}
    get_completion.assert_awaited_once()
