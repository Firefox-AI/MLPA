import asyncio
from unittest.mock import AsyncMock

import pytest

from mlpa.core.pg_services.pg_service import PGService


def _make_service(pool, connected=True):
    svc = PGService("test_db")
    svc.pg = pool
    svc.connected = connected
    return svc


async def test_ping_true_when_select_succeeds():
    pool = AsyncMock()
    pool.fetchval.return_value = 1
    svc = _make_service(pool)

    assert await svc.ping() is True
    pool.fetchval.assert_awaited_once_with("SELECT 1")


async def test_ping_false_when_query_raises():
    pool = AsyncMock()
    pool.fetchval.side_effect = OSError("connection refused")
    svc = _make_service(pool)

    assert await svc.ping() is False


async def test_ping_false_on_timeout():
    async def _slow(*args, **kwargs):
        await asyncio.sleep(1)

    pool = AsyncMock()
    pool.fetchval.side_effect = _slow
    svc = _make_service(pool)

    assert await svc.ping(timeout_s=0.01) is False


async def test_ping_false_when_pool_none():
    svc = _make_service(None, connected=False)
    assert await svc.ping() is False


async def test_ping_honors_explicit_zero_timeout():
    # 0.0 must not be treated as falsy and replaced by the default: an expired
    # budget fires at the first await point rather than running the full default.
    async def _yield_then_return(*args, **kwargs):
        await asyncio.sleep(0)
        return 1

    pool = AsyncMock()
    pool.fetchval.side_effect = _yield_then_return
    svc = _make_service(pool)

    assert await svc.ping(timeout_s=0.0) is False
