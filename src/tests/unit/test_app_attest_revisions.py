import asyncio
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from mlpa.core.pg_services.app_attest_pg_service import AppAttestPGService


def _make_service(pool):
    svc = AppAttestPGService(MagicMock())
    svc.pg = pool
    svc.connected = True
    return svc


async def test_current_revisions_return_db_values():
    pool = AsyncMock()
    pool.fetch.return_value = [
        {"version_num": "919c4d382c42"},
        {"version_num": "abc123"},
    ]
    svc = _make_service(pool)

    assert await svc.current_revisions() == {"919c4d382c42", "abc123"}


async def test_current_revisions_empty_when_table_absent():
    pool = AsyncMock()
    pool.fetch.side_effect = asyncpg.UndefinedTableError("no table")
    svc = _make_service(pool)

    assert await svc.current_revisions() == set()


async def test_current_revisions_raise_on_connection_error():
    pool = AsyncMock()
    pool.fetch.side_effect = OSError("connection refused")
    svc = _make_service(pool)

    with pytest.raises(OSError):
        await svc.current_revisions()


async def test_current_revisions_raise_when_pool_not_connected():
    svc = _make_service(MagicMock())
    svc.pg = None
    svc.connected = False

    with pytest.raises(ConnectionError):
        await svc.current_revisions()


async def test_current_revisions_raise_on_timeout():
    async def _slow(*args, **kwargs):
        await asyncio.sleep(1)

    pool = AsyncMock()
    pool.fetch.side_effect = _slow
    svc = _make_service(pool)

    with pytest.raises(TimeoutError):
        await svc.current_revisions(timeout_s=0.01)
