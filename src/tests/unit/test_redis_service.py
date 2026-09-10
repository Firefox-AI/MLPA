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
        feature,
        basket_field,
        feature_limit,
        basket_limit,
        ttl_seconds,
        inc_amount,
    ):
        assert num_keys == 1
        assert "HINCRBY" in script

        bucket = self.hashes.setdefault(key, {})
        feature_count = int(bucket.get(feature, 0)) + int(inc_amount)
        basket_count = int(bucket.get(basket_field, 0)) + int(inc_amount)
        feature_limit = int(feature_limit)
        basket_limit = int(basket_limit)

        bucket[feature] = feature_count
        bucket[basket_field] = basket_count
        self.expirations[key] = int(ttl_seconds)

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

        return [1, bucket[feature], bucket[basket_field], "", "normal", ""]

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)


async def test_check_and_increment_feature_rpm_records_minute_bucket():
    redis = FakeRedis()
    service = RedisService()
    service.redis = redis

    decision = await service.check_and_increment_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        feature_rpm_limit=10,
        basket_rpm_limit=20,
        ttl_seconds=120,
        now=125,
    )

    key = "mlpa:traffic_contract:rpm:120"
    assert decision.allowed is True
    assert decision.feature_count == 1
    assert decision.basket_count == 1
    assert decision.retry_after_seconds == 55
    assert decision.limited_by is None
    assert decision.mode == "normal"
    assert decision.ratio_over is None
    assert redis.hashes[key]["smart-window"] == 1
    assert redis.hashes[key][TRAFFIC_CONTRACT_BASKET_FIELD] == 1
    assert redis.expirations[key] == 120
    assert (
        await service.get_current_feature_rpm(
            key_prefix="mlpa:traffic_contract",
            feature="smart-window",
            now=125,
        )
        == 1
    )


async def test_check_and_increment_feature_rpm_marks_borrowed_over_feature_limit():
    service = RedisService()
    service.redis = FakeRedis()

    allowed = await service.check_and_increment_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        feature_rpm_limit=1,
        basket_rpm_limit=10,
        now=125,
    )
    rejected = await service.check_and_increment_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        feature_rpm_limit=1,
        basket_rpm_limit=10,
        now=125,
    )

    assert allowed.allowed is True
    assert rejected.allowed is False
    assert rejected.limited_by == "feature"
    assert rejected.mode == "borrowed"
    assert rejected.ratio_over == 2.0
    assert rejected.feature_count == 2
    assert rejected.basket_count == 2


async def test_check_and_increment_feature_rpm_marks_degraded_over_basket_limit():
    service = RedisService()
    service.redis = FakeRedis()

    allowed = await service.check_and_increment_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        feature_rpm_limit=10,
        basket_rpm_limit=1,
        now=125,
    )
    rejected = await service.check_and_increment_feature_rpm(
        key_prefix="mlpa:traffic_contract",
        feature="memories",
        feature_rpm_limit=10,
        basket_rpm_limit=1,
        now=125,
    )

    assert allowed.allowed is True
    assert rejected.allowed is False
    assert rejected.limited_by == "basket"
    assert rejected.mode == "degraded"
    assert rejected.ratio_over == 2.0
    assert rejected.feature_count == 1
    assert rejected.basket_count == 2
