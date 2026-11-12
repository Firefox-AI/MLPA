import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mlpa.core.services.litellm_pg_service import LiteLLMPGService
from mlpa.core.services.pg_service import PGService

USER_ID = "fxa-uid:ai"
USER = {"user_id": USER_ID, "blocked": False}


@pytest.fixture
def conn():
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock()
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    return conn


@pytest.fixture
def pool(mocker, conn):
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    pool.fetchrow = AsyncMock()
    mocker.patch.object(PGService, "pool", new=pool)
    return pool


async def test_get_user_caches_existing_user(pool):
    pool.fetchrow.return_value = dict(USER)
    service = LiteLLMPGService()

    (await service.get_user(USER_ID))["blocked"] = True  # callers get copies

    assert await service.get_user(USER_ID) == USER
    pool.fetchrow.assert_awaited_once()


async def test_get_user_does_not_cache_missing_user(pool):
    """A miss must reach the DB again so a just-created user is found."""
    pool.fetchrow.side_effect = [None, dict(USER)]
    service = LiteLLMPGService()

    assert await service.get_user(USER_ID) is None
    assert await service.get_user(USER_ID) == USER


async def test_fresh_get_user_does_not_join_lookup_started_before_create(pool):
    release_stale = asyncio.Event()

    async def fetchrow(_sql, _user_id):
        if pool.fetchrow.await_count == 1:
            await release_stale.wait()
            return None  # read before the user was created
        return dict(USER)

    pool.fetchrow.side_effect = fetchrow
    service = LiteLLMPGService()

    stale_lookup = asyncio.create_task(service.get_user(USER_ID))
    await asyncio.sleep(0)
    fresh_read = asyncio.create_task(service.get_user(USER_ID, fresh=True))
    await asyncio.sleep(0)
    release_stale.set()

    assert await fresh_read == USER
    assert await stale_lookup is None


async def test_block_user_invalidates_cached_user(pool, conn):
    pool.fetchrow.side_effect = [dict(USER), {**USER, "blocked": True}]
    conn.fetchrow.return_value = {**USER, "blocked": True}
    service = LiteLLMPGService()

    await service.get_user(USER_ID)
    await service.block_user(USER_ID)

    assert (await service.get_user(USER_ID))["blocked"] is True


async def test_count_users_by_service_type_is_cached(pool, conn):
    conn.fetch.return_value = [{"service_type": "ai", "total_users": 2}]
    service = LiteLLMPGService()

    await service.count_users_by_service_type()
    counts = await service.count_users_by_service_type()

    assert counts == {"service_type_counts": {"ai": 2}, "total_users": 2}
    conn.fetch.assert_awaited_once()
