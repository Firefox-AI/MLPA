import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Hashable

from mlpa.core.prometheus_metrics import CacheResult, metrics


class AsyncTTLCache[K: Hashable, V]:
    """Per-process TTL + LRU cache for async loaders.

    - Concurrent misses for a key share one load (single-flight). The load runs
      in its own task, so a cancelled caller doesn't cancel it for the others.
    - A loader result of None is returned but not cached; exceptions propagate
      and are not cached either.
    - invalidate() drops the entry and detaches any in-flight load, so a load
      that read the old value can't store it afterwards.
    - ttl_s <= 0 disables caching and every call runs the loader.

    Entries are per pod: invalidate() only affects this process, other pods
    keep their copy until it expires.
    """

    def __init__(
        self,
        name: str,
        ttl_s: float,
        maxsize: int,
        timer: Callable[[], float] = time.monotonic,
    ):
        self._name = name
        self._ttl_s = ttl_s
        self._maxsize = max(maxsize, 1)
        self._timer = timer
        self._entries: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._inflight: dict[K, asyncio.Task[V | None]] = {}

    def __len__(self) -> int:
        return len(self._entries)

    async def get(self, key: K, loader: Callable[[], Awaitable[V | None]]) -> V | None:
        if self._ttl_s <= 0:
            return await loader()

        entry = self._entries.get(key)
        if entry is not None:
            expires_at, value = entry
            if self._timer() < expires_at:
                self._entries.move_to_end(key)
                self._record(CacheResult.HIT)
                return value
            del self._entries[key]

        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._load(key, loader))
            # Consume the exception if every waiter was cancelled, so asyncio
            # doesn't log "Task exception was never retrieved".
            task.add_done_callback(_consume_exception)
            self._inflight[key] = task
            self._record(CacheResult.MISS)
        else:
            self._record(CacheResult.COALESCED)
        return await asyncio.shield(task)

    def invalidate(self, key: K) -> None:
        self._entries.pop(key, None)
        self._inflight.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()
        self._inflight.clear()

    async def _load(
        self, key: K, loader: Callable[[], Awaitable[V | None]]
    ) -> V | None:
        current = asyncio.current_task()
        try:
            value = await loader()
            if value is not None and self._inflight.get(key) is current:
                self._entries[key] = (self._timer() + self._ttl_s, value)
                self._entries.move_to_end(key)
                while len(self._entries) > self._maxsize:
                    self._entries.popitem(last=False)
            return value
        finally:
            if self._inflight.get(key) is current:
                del self._inflight[key]

    def _record(self, result: CacheResult) -> None:
        metrics.cache_lookups_total.labels(cache=self._name, result=result).inc()


def _consume_exception(task: asyncio.Task) -> None:
    if not task.cancelled():
        task.exception()
