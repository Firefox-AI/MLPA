import asyncio

import pytest

from mlpa.core.ttl_cache import AsyncTTLCache


class FakeClock:
    def __init__(self):
        self.now = 1000.0

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

    assert await cache.get("k", loader) == "v"
    clock.now += 29.9
    assert await cache.get("k", loader) == "v"
    assert loader.calls == 1

    clock.now += 0.1
    assert await cache.get("k", loader) == "v"
    assert loader.calls == 2

    assert metrics_spy.value("cache_lookups_total", cache="test", result="miss") == 2
    assert metrics_spy.value("cache_lookups_total", cache="test", result="hit") == 1


async def test_none_is_returned_but_not_cached():
    cache = _cache()
    loader = CountingLoader(value=None)

    assert await cache.get("k", loader) is None
    assert await cache.get("k", loader) is None
    assert loader.calls == 2
    assert len(cache) == 0


async def test_exception_propagates_and_is_not_cached():
    cache = _cache()
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("db down")
        return "ok"

    with pytest.raises(RuntimeError):
        await cache.get("k", flaky)
    assert await cache.get("k", flaky) == "ok"
    assert calls == 2


async def test_concurrent_misses_share_one_load(metrics_spy):
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
    assert metrics_spy.value("cache_lookups_total", cache="test", result="miss") == 1
    assert (
        metrics_spy.value("cache_lookups_total", cache="test", result="coalesced") == 4
    )


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
    # The load still completed and populated the cache.
    assert len(cache) == 1


async def test_invalidate_drops_entry():
    cache = _cache()
    loader = CountingLoader()

    await cache.get("k", loader)
    cache.invalidate("k")
    await cache.get("k", loader)
    assert loader.calls == 2


async def test_invalidate_during_load_does_not_store_stale_value():
    cache = _cache()
    release = asyncio.Event()

    async def stale():
        await release.wait()
        return "old"

    pending = asyncio.create_task(cache.get("k", stale))
    await asyncio.sleep(0)
    # A write lands while the read is in flight.
    cache.invalidate("k")
    release.set()
    assert await pending == "old"

    fresh = CountingLoader(value="new")
    assert await cache.get("k", fresh) == "new"
    assert fresh.calls == 1


async def test_lru_eviction_bounds_size():
    cache = _cache(maxsize=2)
    a, b, c = CountingLoader("a"), CountingLoader("b"), CountingLoader("c")

    await cache.get("a", a)
    await cache.get("b", b)
    await cache.get("a", a)  # touch "a" so "b" is least recently used
    await cache.get("c", c)

    assert len(cache) == 2
    await cache.get("a", a)
    await cache.get("b", b)
    assert a.calls == 1
    assert b.calls == 2


async def test_zero_ttl_disables_caching(metrics_spy):
    cache = _cache(ttl_s=0)
    loader = CountingLoader()

    await cache.get("k", loader)
    await cache.get("k", loader)
    assert loader.calls == 2
    assert len(cache) == 0
    metrics_spy.assert_only(set())
