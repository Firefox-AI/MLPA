import time
from dataclasses import dataclass
from typing import Any, Literal, cast

import redis.asyncio as aioredis

from mlpa.core.config import env
from mlpa.core.logger import logger

TRAFFIC_CONTRACT_BASKET_FIELD = "__basket__"

_CHECK_AND_INCREMENT_SCRIPT = """
local key = KEYS[1]
local feature_field = ARGV[1]
local basket_field = ARGV[2]
local feature_limit = tonumber(ARGV[3])
local basket_limit = tonumber(ARGV[4])
local ttl_seconds = tonumber(ARGV[5])
local inc_amount = tonumber(ARGV[6])

local feature_count = redis.call("HINCRBY", key, feature_field, inc_amount)
local basket_count = redis.call("HINCRBY", key, basket_field, inc_amount)
redis.call("EXPIRE", key, ttl_seconds)

if basket_limit > 0 and basket_count > basket_limit then
    return {0, feature_count, basket_count, "basket", "degraded", tostring(basket_count/basket_limit)}
end

if feature_limit > 0 and feature_count > feature_limit then
    return {0, feature_count, basket_count, "feature", "borrowed", tostring(feature_count/feature_limit)}
end

return {1, feature_count, basket_count, "", "normal", ""}
"""

TrafficContractMode = Literal["normal", "borrowed", "degraded"]


@dataclass(frozen=True)
class TrafficContractDecision:
    allowed: bool
    feature_count: int
    basket_count: int
    retry_after_seconds: int
    limited_by: str | None = None

    # borrowed = feature is over limit, basket has room
    # degraded = basket is over limit
    mode: TrafficContractMode = "normal"
    ratio_over: float | None = None  # ratio of count / limit if mode != "normal"


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
        key_type: Literal["rpm", "tpm"],
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

    async def check_and_increment_feature_rpm(
        self,
        *,
        key_prefix: str,
        feature: str,
        feature_rpm_limit: int,
        window_seconds: int = 60,
        ttl_seconds: int = 120,
        now: int | None = None,
    ) -> TrafficContractDecision:
        key = self.traffic_contract_key(
            key_prefix=key_prefix,
            key_type="rpm",
            now=now,
            window_seconds=window_seconds,
        )
        retry_after = self.retry_after_seconds(
            now=now,
            window_seconds=window_seconds,
        )

        result = await self.client.eval(
            _CHECK_AND_INCREMENT_SCRIPT,
            1,
            key,
            feature,
            TRAFFIC_CONTRACT_BASKET_FIELD,
            feature_rpm_limit,
            env.TOTAL_TRAFFIC_CONTRACT_RPM_LIMIT,
            ttl_seconds,
            1,
        )

        return TrafficContractDecision(
            allowed=bool(int(result[0])),
            feature_count=int(result[1]),
            basket_count=int(result[2]),
            retry_after_seconds=retry_after,
            limited_by=str(result[3]) or None,
            mode=cast(TrafficContractMode, result[4]),
            ratio_over=float(result[5]) if result[5] not in (None, "") else None,
        )

    async def get_current_feature_rpm(
        self,
        *,
        key_prefix: str,
        feature: str,
        window_seconds: int = 60,
        now: int | None = None,
    ) -> int:
        key = self.traffic_contract_key(
            key_prefix=key_prefix,
            key_type="rpm",
            now=now,
            window_seconds=window_seconds,
        )
        traffic_count = await self.client.hget(key, feature)
        return int(traffic_count) if traffic_count else 0
