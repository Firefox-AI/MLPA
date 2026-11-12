import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from mlpa.core.config import env
from mlpa.core.services.litellm_pg_service import LiteLLMPGService
from mlpa.core.services.pg_service import PGService

USER_ID = "fxa-uid:ai"


def _conn():
    conn = MagicMock()
    conn.execute = AsyncMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchrow = AsyncMock()
    conn.fetchval = AsyncMock()
    conn.transaction.return_value.__aenter__ = AsyncMock(return_value=None)
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=None)
    return conn


def _pool(conn):
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool = MagicMock()
    pool.acquire.return_value = acquire_cm
    pool.fetchrow = AsyncMock()
    pool.fetchval = AsyncMock()
    return pool


@pytest.fixture
def conn():
    return _conn()


@pytest.fixture
def pool(mocker, conn):
    pool = _pool(conn)
    mocker.patch.object(PGService, "pool", new=pool)
    return pool


async def test_get_user_serves_repeat_lookups_from_cache(pool, metrics_spy):
    pool.fetchrow.return_value = {"user_id": USER_ID, "blocked": False}
    service = LiteLLMPGService()

    first = await service.get_user(USER_ID)
    second = await service.get_user(USER_ID)

    assert first == second == {"user_id": USER_ID, "blocked": False}
    pool.fetchrow.assert_awaited_once()
    assert metrics_spy.value("cache_lookups_total", cache="get_user", result="hit") == 1


async def test_get_user_returns_copies(pool):
    pool.fetchrow.return_value = {"user_id": USER_ID, "blocked": False}
    service = LiteLLMPGService()

    (await service.get_user(USER_ID))["blocked"] = True

    assert (await service.get_user(USER_ID))["blocked"] is False


async def test_get_user_does_not_cache_missing_user(pool):
    """A miss must hit the DB again so a just-created user is found."""
    pool.fetchrow.side_effect = [None, {"user_id": USER_ID, "blocked": False}]
    service = LiteLLMPGService()

    assert await service.get_user(USER_ID) is None
    assert await service.get_user(USER_ID) == {"user_id": USER_ID, "blocked": False}
    assert pool.fetchrow.await_count == 2


async def test_fresh_get_user_does_not_join_lookup_started_before_create(pool):
    """A lookup that began before the user was created must not answer the
    post-create re-read with its stale miss."""
    release_stale = asyncio.Event()
    created = {"user_id": USER_ID, "blocked": False}

    async def fetchrow(_sql, _user_id):
        if pool.fetchrow.await_count == 1:
            await release_stale.wait()
            return None  # snapshot from before the create committed
        return created

    pool.fetchrow.side_effect = fetchrow
    service = LiteLLMPGService()

    stale_lookup = asyncio.create_task(service.get_user(USER_ID))
    await asyncio.sleep(0)

    fresh_read = asyncio.create_task(service.get_user(USER_ID, fresh=True))
    await asyncio.sleep(0)
    release_stale.set()

    assert await fresh_read == created
    assert await stale_lookup is None
    # The fresh row is what later lookups see.
    assert await service.get_user(USER_ID) == created
    assert pool.fetchrow.await_count == 2


async def test_get_user_cache_disabled_with_zero_ttl(pool, mocker):
    mocker.patch.object(env, "GET_USER_CACHE_TTL_S", 0)
    pool.fetchrow.return_value = {"user_id": USER_ID, "blocked": False}
    service = LiteLLMPGService()

    await service.get_user(USER_ID)
    await service.get_user(USER_ID)

    assert pool.fetchrow.await_count == 2


@pytest.mark.parametrize("blocked", [True, False])
async def test_block_user_invalidates_cached_user(pool, conn, blocked):
    pool.fetchrow.side_effect = [
        {"user_id": USER_ID, "blocked": not blocked},
        {"user_id": USER_ID, "blocked": blocked},
    ]
    conn.fetchrow.return_value = {"user_id": USER_ID, "blocked": blocked}
    service = LiteLLMPGService()

    await service.get_user(USER_ID)
    await service.block_user(USER_ID, blocked=blocked)

    assert (await service.get_user(USER_ID))["blocked"] is blocked
    assert pool.fetchrow.await_count == 2


async def test_block_user_not_found_still_invalidates(pool, conn):
    pool.fetchrow.return_value = {"user_id": USER_ID, "blocked": False}
    conn.fetchrow.return_value = None
    service = LiteLLMPGService()

    await service.get_user(USER_ID)
    with pytest.raises(HTTPException):
        await service.block_user(USER_ID)
    await service.get_user(USER_ID)

    assert pool.fetchrow.await_count == 2


async def test_update_user_budget_invalidates_cached_user(pool, conn):
    pool.fetchrow.side_effect = [
        {"user_id": USER_ID, "budget_id": "old"},
        {"user_id": USER_ID, "budget_id": "new"},
    ]
    conn.fetchrow.return_value = {"user_id": USER_ID, "budget_id": "new"}
    service = LiteLLMPGService()

    await service.get_user(USER_ID)
    await service.update_user_budget(USER_ID, "new")

    assert (await service.get_user(USER_ID))["budget_id"] == "new"


async def test_has_managed_user_rows_matches_on_primary_key(pool):
    pool.fetchval.return_value = True
    service = LiteLLMPGService()

    assert await service.has_managed_user_rows("base", ["ai", "memories"]) is True

    sql, user_ids = pool.fetchval.call_args.args
    assert "split_part" not in sql
    assert "user_id = ANY($1::text[])" in sql
    assert user_ids == ["base:ai", "base:memories"]


async def test_count_users_by_service_type_is_cached(pool, conn, metrics_spy):
    conn.fetch.return_value = [
        {"service_type": "ai", "total_users": 2},
        {"service_type": "s2s", "total_users": 1},
    ]
    service = LiteLLMPGService()

    first = await service.count_users_by_service_type()
    second = await service.count_users_by_service_type()

    assert (
        first
        == second
        == {
            "service_type_counts": {"ai": 2, "s2s": 1},
            "total_users": 3,
        }
    )
    conn.fetch.assert_awaited_once()
    assert (
        metrics_spy.value(
            "cache_lookups_total", cache="user_counts_by_service_type", result="hit"
        )
        == 1
    )


async def test_count_users_by_service_type_error_is_not_cached(pool, conn):
    conn.fetch.side_effect = [RuntimeError("timeout"), []]
    service = LiteLLMPGService()

    with pytest.raises(HTTPException) as exc_info:
        await service.count_users_by_service_type()
    assert exc_info.value.status_code == 500

    assert await service.count_users_by_service_type() == {
        "service_type_counts": {},
        "total_users": 0,
    }
