from unittest.mock import call

from mlpa.core.config import env
from mlpa.core.consts import TrafficContractKeyType, TrafficContractMode
from mlpa.core.services.redis_service import (
    TRAFFIC_CONTRACT_BASKET_FIELD,
    RedisService,
)


class FakeRedis:
    def __init__(self):
        self.hashes = {}
        self.expirations = {}

    async def eval(
        self,
        script,
        num_keys,
        key,
        *args,
    ):
        assert num_keys == 1
        if "HINCRBY" in script:
            feature, basket_field, ttl_seconds, inc_amount = args
            bucket = self.hashes.setdefault(key, {})
            if int(inc_amount) <= 0:
                return [
                    int(bucket.get(feature, 0)),
                    int(bucket.get(basket_field, 0)),
                ]

            feature_count = int(bucket.get(feature, 0)) + int(inc_amount)
            basket_count = int(bucket.get(basket_field, 0)) + int(inc_amount)
            bucket[feature] = feature_count
            bucket[basket_field] = basket_count
            self.expirations[key] = int(ttl_seconds)
            return [feature_count, basket_count]

        feature, basket_field, feature_limit, basket_limit, inc_amount = args
        bucket = self.hashes.get(key, {})
        feature_limit = int(feature_limit)
        basket_limit = int(basket_limit)
        if basket_limit == 0 and feature_limit == 0:
            return [1, 0, 0, "", "normal", ""]

        feature_count = int(bucket.get(feature, 0)) + int(inc_amount)
        basket_count = int(bucket.get(basket_field, 0)) + int(inc_amount)

        if basket_limit > 0 and basket_count > basket_limit:
            return [
                0,
                feature_count,
                basket_count,
                "basket",
                "degraded",
                str(basket_count / basket_limit),
            ]

        if feature_limit > 0 and feature_count > feature_limit:
            return [
                0,
                feature_count,
                basket_count,
                "feature",
                "borrowed",
                str(feature_count / feature_limit),
            ]

        return [1, feature_count, basket_count, "", "normal", ""]

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)


async def test_check_feature_rpm_does_not_mutate_bucket(mocker):
    mocker.patch.object(env, "TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT", 20)
    redis = FakeRedis()
    service = RedisService()
    service.redis = redis

    decision = await service.check_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        feature_rpm_limit=10,
        now=125,
    )

    assert decision.allowed is True
    assert decision.feature_count == 1
    assert decision.basket_count == 1
    assert decision.retry_after_seconds == 55
    assert decision.limited_by is None
    assert decision.mode == TrafficContractMode.NORMAL
    assert decision.ratio_over is None
    assert redis.hashes == {}
    assert redis.expirations == {}


async def test_increment_feature_rpm_records_minute_bucket():
    redis = FakeRedis()
    service = RedisService()
    service.redis = redis

    counters = await service.increment_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        ttl_seconds=120,
        now=125,
    )

    key = "mlpa:traffic_contract:rpm:120"
    assert counters.feature_count == 1
    assert counters.basket_count == 1
    assert redis.hashes[key]["smart-window"] == 1
    assert redis.hashes[key][TRAFFIC_CONTRACT_BASKET_FIELD] == 1
    assert redis.expirations[key] == 120


async def test_increment_feature_tpm_records_token_bucket():
    redis = FakeRedis()
    service = RedisService()
    service.redis = redis

    counters = await service.increment_feature_tpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        token_count=37,
        ttl_seconds=120,
        now=125,
    )

    key = "mlpa:traffic_contract:tpm:120"
    assert counters.feature_count == 37
    assert counters.basket_count == 37
    assert redis.hashes[key]["smart-window"] == 37
    assert redis.hashes[key][TRAFFIC_CONTRACT_BASKET_FIELD] == 37
    assert redis.expirations[key] == 120


async def test_check_feature_rpm_marks_borrowed_over_feature_limit(mocker):
    mocker.patch.object(env, "TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT", 10)
    redis = FakeRedis()
    service = RedisService()
    service.redis = redis

    await service.increment_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        now=125,
    )
    decision = await service.check_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        feature_rpm_limit=1,
        now=125,
    )

    key = "mlpa:traffic_contract:rpm:120"
    assert decision.allowed is False
    assert decision.limited_by == "feature"
    assert decision.mode == TrafficContractMode.BORROWED
    assert decision.ratio_over == 2.0
    assert decision.feature_count == 2
    assert decision.basket_count == 2
    assert redis.hashes[key]["smart-window"] == 1
    assert redis.hashes[key][TRAFFIC_CONTRACT_BASKET_FIELD] == 1


async def test_check_feature_rpm_marks_degraded_over_basket_limit(mocker):
    mocker.patch.object(env, "TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT", 1)
    redis = FakeRedis()
    service = RedisService()
    service.redis = redis

    await service.increment_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        now=125,
    )
    decision = await service.check_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="s2s",
        feature_rpm_limit=10,
        now=125,
    )

    key = "mlpa:traffic_contract:rpm:120"
    assert decision.allowed is False
    assert decision.limited_by == "basket"
    assert decision.mode == TrafficContractMode.DEGRADED
    assert decision.ratio_over == 2.0
    assert decision.feature_count == 1
    assert decision.basket_count == 2
    assert "s2s" not in redis.hashes[key]
    assert redis.hashes[key][TRAFFIC_CONTRACT_BASKET_FIELD] == 1


async def test_update_contracts_increments_rpm_and_tpm_with_usage(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    service = RedisService()
    increment = mocker.patch.object(
        service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )

    await service.update_contracts(
        service_type="ai",
        usage={"total_tokens": 37},
    )

    increment.assert_has_awaits(
        [
            call(
                key_type=TrafficContractKeyType.RPM,
                service_type="ai",
                increment_amount=1,
            ),
            call(
                key_type=TrafficContractKeyType.TPM,
                service_type="ai",
                increment_amount=37,
            ),
        ],
        any_order=True,
    )
    assert increment.await_count == 2


async def test_update_contracts_increments_only_rpm_without_usage(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    service = RedisService()
    increment = mocker.patch.object(
        service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )

    await service.update_contracts(service_type="ai", usage=None)

    increment.assert_awaited_once_with(
        key_type=TrafficContractKeyType.RPM,
        service_type="ai",
        increment_amount=1,
    )


async def test_update_contracts_noops_when_disabled(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", False)
    service = RedisService()
    increment = mocker.patch.object(
        service,
        "inc_traffic_contract",
        mocker.AsyncMock(),
    )

    await service.update_contracts(
        service_type="ai",
        usage={"total_tokens": 37},
    )

    increment.assert_not_awaited()


async def test_inc_traffic_contract_noops_for_unknown_service_type(mocker):
    mocker.patch.object(env, "ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT", True)
    service = RedisService()
    increment = mocker.patch.object(
        service,
        "increment_feature_traffic_contract",
        mocker.AsyncMock(),
    )

    result = await service.inc_traffic_contract(
        key_type=TrafficContractKeyType.RPM,
        service_type="unknown-service",
        increment_amount=1,
    )

    assert result is None
    increment.assert_not_awaited()
