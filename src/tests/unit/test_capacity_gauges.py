import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mlpa.core.config import GUEST_CAPACITY_ID, USER_CAPACITY_ID, env
from mlpa.core.services.app_attest_pg_service import AppAttestPGService


@pytest.fixture
def service():
    svc = AppAttestPGService(MagicMock())
    svc.pg = MagicMock()
    svc.pg.fetch = AsyncMock(
        return_value=[
            {"id": USER_CAPACITY_ID, "max_identities": 100, "current_identities": 7},
            {"id": GUEST_CAPACITY_ID, "max_identities": 50, "current_identities": 3},
        ]
    )
    return svc


async def test_refresh_sets_gauges_from_db(service, metrics_spy, mocker):
    mocker.patch.object(env, "MLPA_ENFORCE_GUEST_CAP", True)
    mocker.patch.object(env, "MLPA_ENFORCE_SIGNIN_CAP", False)
    mocker.patch.object(env, "MLPA_GUEST_ENABLED", True)
    await service.refresh_capacity_gauges()
    assert metrics_spy.value("capacity_current_identities", pool="user") == 7
    assert metrics_spy.value("capacity_current_identities", pool="guest") == 3
    assert metrics_spy.value("capacity_max_identities", pool="user") == 100
    assert metrics_spy.value("capacity_max_identities", pool="guest") == 50
    assert metrics_spy.value("capacity_enforced", pool="guest") == 1
    assert metrics_spy.value("capacity_enforced", pool="user") == 0


async def test_refresh_db_error_keeps_last_values(service, metrics_spy):
    await service.refresh_capacity_gauges()
    service.pg.fetch.side_effect = Exception("db down")
    await service.refresh_capacity_gauges()
    assert metrics_spy.value("capacity_current_identities", pool="user") == 7


async def test_refresh_hung_db_times_out_and_keeps_last_values(
    service, metrics_spy, mocker
):
    await service.refresh_capacity_gauges()
    mocker.patch(
        "mlpa.core.services.app_attest_pg_service.CAPACITY_GAUGE_REFRESH_TIMEOUT_S",
        0.05,
    )

    async def hang(*_args, **_kwargs):
        await asyncio.sleep(3600)

    service.pg.fetch.side_effect = hang
    service.pg.fetch.reset_mock()
    # Two scrapes while the DB hangs: both return fast, only one query in flight
    await asyncio.wait_for(service.refresh_capacity_gauges(), timeout=1)
    await asyncio.wait_for(service.refresh_capacity_gauges(), timeout=1)
    assert service.pg.fetch.await_count == 1
    assert metrics_spy.value("capacity_current_identities", pool="user") == 7
    service._capacity_refresh.cancel()
