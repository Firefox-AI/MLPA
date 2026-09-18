import asyncio
import time
from typing import Any

import redis.asyncio as aioredis

from mlpa.core.classes import TrafficContractCounters, TrafficContractDecision
from mlpa.core.config import env
from mlpa.core.consts import TrafficContractKeyType, TrafficContractMode
from mlpa.core.logger import logger

TRAFFIC_CONTRACT_BASKET_FIELD = "__basket__"

_CHECK_TRAFFIC_CONTRACTS_SCRIPT = """
local function check(key, feature_field, basket_field, feature_limit, basket_limit, inc_amount)
    if basket_limit == 0 and feature_limit == 0 then
        return {1, 0, 0, "", "normal", ""}
    end

    local feature_count = tonumber(redis.call("HGET", key, feature_field) or "0") + inc_amount
    local basket_count = tonumber(redis.call("HGET", key, basket_field) or "0") + inc_amount

    if basket_limit > 0 and basket_count > basket_limit then
        return {0, feature_count, basket_count, "basket", "degraded", tostring(basket_count/basket_limit)}
    end

    if feature_limit > 0 and feature_count > feature_limit then
        return {0, feature_count, basket_count, "feature", "borrowed", tostring(feature_count/feature_limit)}
    end

    return {1, feature_count, basket_count, "", "normal", ""}
end

local rpm = check(KEYS[1], ARGV[1], ARGV[2], tonumber(ARGV[3]), tonumber(ARGV[4]), tonumber(ARGV[5]))
local tpm = check(KEYS[2], ARGV[1], ARGV[2], tonumber(ARGV[6]), tonumber(ARGV[7]), tonumber(ARGV[8]))

return {rpm, tpm}
"""

_INCREMENT_TRAFFIC_CONTRACT_SCRIPT = """
local key = KEYS[1]
local feature_field = ARGV[1]
local basket_field = ARGV[2]
local ttl_seconds = tonumber(ARGV[3])
local inc_amount = tonumber(ARGV[4])

if inc_amount <= 0 then
    local feature_count = tonumber(redis.call("HGET", key, feature_field) or "0")
    local basket_count = tonumber(redis.call("HGET", key, basket_field) or "0")
    return {feature_count, basket_count}
end

local feature_count = redis.call("HINCRBY", key, feature_field, inc_amount)
local basket_count = redis.call("HINCRBY", key, basket_field, inc_amount)
redis.call("EXPIRE", key, ttl_seconds)

return {feature_count, basket_count}
"""


class RedisService:
    def __init__(self):
        self.redis: Any | None = None

    async def connect(self):
        self.redis = aioredis.Redis(
            host=env.REDIS_HOST,
            port=env.REDIS_PORT,
            decode_responses=True,
        )
        await self.redis.ping()
        logger.info(f"Connected to Redis at {env.REDIS_HOST}:{env.REDIS_PORT}")

    async def set(self, key: str, value: str):
        await self.client.set(key, value)

    async def get(self, key: str) -> str | None:
        return await self.client.get(key)

    async def close(self):
        if self.redis is not None:
            await self.redis.aclose()
            self.redis = None

    @property
    def client(self) -> Any:
        if self.redis is None:
            raise RuntimeError("Redis client is not connected")
        return self.redis

    @staticmethod
    def bucket_start(now: int | None = None, window_seconds: int = 60) -> int:
        current_time = int(time.time()) if now is None else now
        return (current_time // window_seconds) * window_seconds

    @classmethod
    def traffic_contract_key(
        cls,
        *,
        key_prefix: str,
        key_type: TrafficContractKeyType,
        now: int | None = None,
        window_seconds: int = 60,
    ) -> str:
        bucket_start = cls.bucket_start(now, window_seconds)
        return f"{key_prefix}:{key_type}:{bucket_start}"

    @classmethod
    def retry_after_seconds(
        cls, *, now: int | None = None, window_seconds: int = 60
    ) -> int:
        current_time = int(time.time()) if now is None else now
        elapsed_in_bucket = current_time % window_seconds
        return window_seconds - elapsed_in_bucket

    @staticmethod
    def traffic_contract_decision_from_result(
        result: list,
        *,
        retry_after_seconds: int,
    ) -> TrafficContractDecision:
        return TrafficContractDecision(
            allowed=bool(int(result[0])),
            feature_count=int(result[1]),
            basket_count=int(result[2]),
            retry_after_seconds=retry_after_seconds,
            limited_by=str(result[3]) or None,
            mode=TrafficContractMode(result[4]),
            ratio_over=float(result[5]) if result[5] not in (None, "") else None,
        )

    async def check_feature_traffic_contracts(
        self,
        *,
        key_prefix: str,
        feature: str,
        rpm_limit: int,
        tpm_limit: int,
        rpm_basket_limit: int,
        tpm_basket_limit: int,
        rpm_increment_amount: int,
        tpm_increment_amount: int,
        rpm_window_seconds: int = 60,
        tpm_window_seconds: int = 60,
        now: int | None = None,
    ) -> tuple[TrafficContractDecision, TrafficContractDecision]:
        rpm_key = self.traffic_contract_key(
            key_prefix=key_prefix,
            key_type=TrafficContractKeyType.RPM,
            now=now,
            window_seconds=rpm_window_seconds,
        )
        tpm_key = self.traffic_contract_key(
            key_prefix=key_prefix,
            key_type=TrafficContractKeyType.TPM,
            now=now,
            window_seconds=tpm_window_seconds,
        )
        rpm_retry_after = self.retry_after_seconds(
            now=now,
            window_seconds=rpm_window_seconds,
        )
        tpm_retry_after = self.retry_after_seconds(
            now=now,
            window_seconds=tpm_window_seconds,
        )

        rpm_result, tpm_result = await self.client.eval(
            _CHECK_TRAFFIC_CONTRACTS_SCRIPT,
            2,
            rpm_key,
            tpm_key,
            feature,
            TRAFFIC_CONTRACT_BASKET_FIELD,
            rpm_limit,
            rpm_basket_limit,
            rpm_increment_amount,
            tpm_limit,
            tpm_basket_limit,
            tpm_increment_amount,
        )

        return (
            self.traffic_contract_decision_from_result(
                rpm_result,
                retry_after_seconds=rpm_retry_after,
            ),
            self.traffic_contract_decision_from_result(
                tpm_result,
                retry_after_seconds=tpm_retry_after,
            ),
        )

    async def increment_feature_traffic_contract(
        self,
        *,
        key_prefix: str,
        key_type: TrafficContractKeyType,
        feature: str,
        increment_amount: int,
        window_seconds: int = 60,
        ttl_seconds: int = 120,
        now: int | None = None,
    ) -> TrafficContractCounters:
        key = self.traffic_contract_key(
            key_prefix=key_prefix,
            key_type=key_type,
            now=now,
            window_seconds=window_seconds,
        )
        result = await self.client.eval(
            _INCREMENT_TRAFFIC_CONTRACT_SCRIPT,
            1,
            key,
            feature,
            TRAFFIC_CONTRACT_BASKET_FIELD,
            ttl_seconds,
            increment_amount,
        )

        return TrafficContractCounters(
            feature_count=int(result[0]),
            basket_count=int(result[1]),
        )

    async def inc_traffic_contract(
        self,
        *,
        key_type: TrafficContractKeyType,
        service_type: str,
        increment_amount: int,
    ) -> TrafficContractCounters | None:
        if not env.ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT or increment_amount <= 0:
            return None

        contract = env.traffic_contract_config.get(service_type)
        if contract is None:
            return None

        window_seconds = (
            env.TRAFFIC_CONTRACT_RPM_WINDOW_SECONDS
            if key_type == TrafficContractKeyType.RPM
            else env.TRAFFIC_CONTRACT_TPM_WINDOW_SECONDS
        )
        return await self.increment_feature_traffic_contract(
            key_prefix=env.TRAFFIC_CONTRACT_REDIS_KEY_PREFIX,
            key_type=key_type,
            feature=contract["feature"],
            increment_amount=increment_amount,
            window_seconds=window_seconds,
            ttl_seconds=env.TRAFFIC_CONTRACT_COUNTER_TTL_SECONDS,
        )

    async def update_contracts(self, *, service_type: str, usage: dict | None):
        if not env.ENABLE_TRAFFIC_CONTRACT_ENFORCEMENT:
            return

        try:
            updates = [
                self.inc_traffic_contract(
                    key_type=TrafficContractKeyType.RPM,
                    service_type=service_type,
                    increment_amount=1,
                )
            ]
            if usage and usage.get("total_tokens"):
                updates.append(
                    self.inc_traffic_contract(
                        key_type=TrafficContractKeyType.TPM,
                        service_type=service_type,
                        increment_amount=usage["total_tokens"],
                    )
                )

            await asyncio.gather(*updates)
        except Exception as e:
            logger.error(f"Error updating traffic contracts for {service_type}: {e}")
            if not env.TRAFFIC_CONTRACT_FAIL_OPEN_ON_REDIS_ERROR:
                raise
