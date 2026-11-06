import asyncio
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
        self.eval_calls = []

    @staticmethod
    def _check(bucket, feature, basket_field, feature_limit, basket_limit, inc_amount):
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

    async def eval(
        self,
        script,
        num_keys,
        key,
        *args,
    ):
        self.eval_calls.append((num_keys, key, args))
        if num_keys == 2:
            tpm_key = args[0]
            (
                feature,
                basket_field,
                rpm_feature_limit,
                rpm_basket_limit,
                rpm_inc_amount,
                tpm_feature_limit,
                tpm_basket_limit,
                tpm_inc_amount,
            ) = args[1:]
            return [
                self._check(
                    self.hashes.get(key, {}),
                    feature,
                    basket_field,
                    rpm_feature_limit,
                    rpm_basket_limit,
                    rpm_inc_amount,
                ),
                self._check(
                    self.hashes.get(tpm_key, {}),
                    feature,
                    basket_field,
                    tpm_feature_limit,
                    tpm_basket_limit,
                    tpm_inc_amount,
                ),
            ]

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
        return self._check(
            self.hashes.get(key, {}),
            feature,
            basket_field,
            feature_limit,
            basket_limit,
            inc_amount,
        )

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)


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


async def test_check_feature_traffic_contracts_returns_rpm_and_tpm_decisions(mocker):
    mocker.patch.object(env, "TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT", 5)
    mocker.patch.object(env, "TOTAL_TRAFFIC_CONTRACT_TPM_LIMIT", 10)
    redis = FakeRedis()
    service = RedisService()
    service.redis = redis

    await service.increment_feature_traffic_contract(
        key_prefix="mlpa:traffic_contract",
        key_type=TrafficContractKeyType.RPM,
        feature="smart-window",
        increment_amount=2,
        now=125,
    )
    await service.increment_feature_traffic_contract(
        key_prefix="mlpa:traffic_contract",
        key_type=TrafficContractKeyType.TPM,
        feature="smart-window",
        increment_amount=9,
        now=125,
    )

    rpm_decision, tpm_decision = await service.check_feature_traffic_contracts(
        key_prefix="mlpa:traffic_contract",
        feature="smart-window",
        rpm_limit=2,
        tpm_limit=20,
        rpm_basket_limit=env.TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT,
        tpm_basket_limit=env.TOTAL_TRAFFIC_CONTRACT_TPM_LIMIT,
        rpm_increment_amount=1,
        tpm_increment_amount=2,
        now=125,
    )

    rpm_key = "mlpa:traffic_contract:rpm:120"
    tpm_key = "mlpa:traffic_contract:tpm:120"
    assert rpm_decision.allowed is False
    assert rpm_decision.limited_by == "feature"
    assert rpm_decision.mode == TrafficContractMode.BORROWED
    assert rpm_decision.feature_count == 3
    assert rpm_decision.basket_count == 3
    assert rpm_decision.retry_after_seconds == 55
    assert tpm_decision.allowed is False
    assert tpm_decision.limited_by == "basket"
    assert tpm_decision.mode == TrafficContractMode.DEGRADED
    assert tpm_decision.feature_count == 11
    assert tpm_decision.basket_count == 11
    assert tpm_decision.retry_after_seconds == 55
    assert redis.hashes[rpm_key]["smart-window"] == 2
    assert redis.hashes[rpm_key][TRAFFIC_CONTRACT_BASKET_FIELD] == 2
    assert redis.hashes[tpm_key]["smart-window"] == 9
    assert redis.hashes[tpm_key][TRAFFIC_CONTRACT_BASKET_FIELD] == 9
    assert redis.eval_calls[-1] == (
        2,
        rpm_key,
        (
            tpm_key,
            "smart-window",
            TRAFFIC_CONTRACT_BASKET_FIELD,
            2,
            5,
            1,
            20,
            10,
            2,
        ),
    )


# Guest kill switch (AIPLAT-1450)


class FakeKVRedis:
    def __init__(self, values=None, error=None):
        self.values = values or {}
        self.error = error
        self.get_calls = 0

    async def get(self, key):
        self.get_calls += 1
        if self.error:
            raise self.error
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value


def _kill_switch_service(mocker, fake, now=1000.0):
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", True)
    mocker.patch.object(env, "GUEST_KILL_SWITCH_CACHE_TTL_SECONDS", 5)
    clock = mocker.patch(
        "mlpa.core.services.redis_service.time.monotonic", return_value=now
    )
    service = RedisService()
    service.redis = fake
    return service, clock


async def test_guests_enabled_env_off_skips_redis(mocker):
    fake = FakeKVRedis()
    service, _ = _kill_switch_service(mocker, fake)
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", False)
    assert await service.guests_enabled() is False
    assert fake.get_calls == 0


async def test_guests_enabled_follows_flag(mocker):
    fake = FakeKVRedis()
    service, clock = _kill_switch_service(mocker, fake)
    assert await service.guests_enabled() is True

    fake.values[env.GUEST_KILL_SWITCH_REDIS_KEY] = "0"
    clock.return_value += 5
    assert await service.guests_enabled() is False


async def test_guests_enabled_cached_for_ttl(mocker):
    fake = FakeKVRedis({env.GUEST_KILL_SWITCH_REDIS_KEY: "0"})
    service, clock = _kill_switch_service(mocker, fake)
    assert await service.guests_enabled() is False
    fake.values[env.GUEST_KILL_SWITCH_REDIS_KEY] = "1"
    clock.return_value += 4.9
    assert await service.guests_enabled() is False
    assert fake.get_calls == 1
    clock.return_value += 0.1
    assert await service.guests_enabled() is True
    assert fake.get_calls == 2


async def test_set_guests_enabled_applies_on_this_pod_now(mocker):
    fake = FakeKVRedis()
    service, _ = _kill_switch_service(mocker, fake)
    assert await service.guests_enabled() is True
    assert await service.set_guests_enabled(False) is False
    assert fake.values[env.GUEST_KILL_SWITCH_REDIS_KEY] == "0"
    assert await service.guests_enabled() is False


async def test_guests_enabled_fail_open_keeps_last_value(mocker):
    fake = FakeKVRedis({env.GUEST_KILL_SWITCH_REDIS_KEY: "0"})
    service, clock = _kill_switch_service(mocker, fake)
    assert await service.guests_enabled() is False

    fake.error = ConnectionError("redis down")
    clock.return_value += 5
    assert await service.guests_enabled() is False
    # The error result is cached too, a dead Redis isn't hit on every request
    assert await service.guests_enabled() is False
    assert fake.get_calls == 2

    # No value read yet → enabled (MLPA_GUEST_ENABLED decides)
    service, _ = _kill_switch_service(mocker, FakeKVRedis(error=ConnectionError()))
    assert await service.guests_enabled() is True


async def test_guests_enabled_fail_closed(mocker):
    fake = FakeKVRedis(error=ConnectionError("redis down"))
    service, _ = _kill_switch_service(mocker, fake)
    mocker.patch.object(env, "GUEST_KILL_SWITCH_FAIL_OPEN_ON_REDIS_ERROR", False)
    assert await service.guests_enabled() is False


async def test_guests_enabled_hung_redis_times_out(mocker):
    class HungRedis(FakeKVRedis):
        async def get(self, key):
            await asyncio.sleep(10)

    # No clock patch here: asyncio's own timers use time.monotonic
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", True)
    mocker.patch.object(env, "GUEST_KILL_SWITCH_REDIS_TIMEOUT_SECONDS", 0.01)
    service = RedisService()
    service.redis = HungRedis()
    assert await asyncio.wait_for(service.guests_enabled(), 1) is True
