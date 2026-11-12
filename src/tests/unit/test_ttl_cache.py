import asyncio

import pytest

from mlpa.core.ttl_cache import AsyncTTLCache


class FakeClock:
    now = 1000.0

    def __call__(self) -> float:
        return self.now


class CountingLoader:
    def __init__(self, value="v"):
        self.value = value
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        return self.value


def _cache(clock=None, ttl_s=30.0, maxsize=100) -> AsyncTTLCache:
    return AsyncTTLCache(
        "test", ttl_s=ttl_s, maxsize=maxsize, timer=clock or FakeClock()
    )


async def test_hit_within_ttl_then_reload_after_expiry(metrics_spy):
    clock = FakeClock()
    cache = _cache(clock)
    loader = CountingLoader()

    await cache.get("k", loader)
    clock.now += 29.9
    await cache.get("k", loader)
    assert loader.calls == 1

    clock.now += 0.1
    await cache.get("k", loader)
    assert loader.calls == 2
    assert metrics_spy.value("cache_lookups_total", cache="test", result="miss") == 2
    assert metrics_spy.value("cache_lookups_total", cache="test", result="hit") == 1


async def test_none_and_errors_are_not_cached():
    cache = _cache()
    missing = CountingLoader(value=None)
    await cache.get("k", missing)
    await cache.get("k", missing)
    assert missing.calls == 2

    async def failing():
        raise RuntimeError("db down")

    with pytest.raises(RuntimeError):
        await cache.get("k", failing)
    assert await cache.get("k", CountingLoader("ok")) == "ok"


async def test_concurrent_misses_share_one_load():
    cache = _cache()
    release = asyncio.Event()
    calls = 0

    async def slow():
        nonlocal calls
        calls += 1
        await release.wait()
        return "v"

    waiters = [asyncio.create_task(cache.get("k", slow)) for _ in range(5)]
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(*waiters) == ["v"] * 5
    assert calls == 1


async def test_cancelled_caller_does_not_cancel_shared_load():
    cache = _cache()
    release = asyncio.Event()

    async def slow():
        await release.wait()
        return "v"

    first = asyncio.create_task(cache.get("k", slow))
    second = asyncio.create_task(cache.get("k", slow))
    await asyncio.sleep(0)
    first.cancel()
    release.set()

    assert await second == "v"
    with pytest.raises(asyncio.CancelledError):
        await first


async def test_invalidate_during_load_does_not_store_stale_value():
    cache = _cache()
    release = asyncio.Event()

    async def stale():
        await release.wait()
        return "old"

    pending = asyncio.create_task(cache.get("k", stale))
    await asyncio.sleep(0)
    cache.invalidate("k")  # a write lands while the read is in flight
    release.set()
    await pending

    assert await cache.get("k", CountingLoader("new")) == "new"


async def test_evicts_least_recently_used():
    cache = _cache(maxsize=2)
    a, b, c = CountingLoader("a"), CountingLoader("b"), CountingLoader("c")

    await cache.get("a", a)
    await cache.get("b", b)
    await cache.get("a", a)  # "b" is now least recently used
    await cache.get("c", c)
    await cache.get("a", a)
    await cache.get("b", b)

    assert (a.calls, b.calls) == (1, 2)


async def test_zero_ttl_disables_caching():
    cache = _cache(ttl_s=0)
    loader = CountingLoader()

    await cache.get("k", loader)
    await cache.get("k", loader)
    assert loader.calls == 2
