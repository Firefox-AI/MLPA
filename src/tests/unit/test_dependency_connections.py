import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
import redis.asyncio as aioredis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import AuthenticationError, MaxConnectionsError
from redis.exceptions import TimeoutError as RedisTimeoutError

from mlpa.core.config import env
from mlpa.core.services.metrics import (
    MonitoredRedis,
    MonitoredRedisConnection,
    MonitoredRedisPool,
)
from mlpa.core.services.pg_service import PGService
from mlpa.core.services.redis_service import RedisService


@pytest.mark.parametrize(
    "db,dependency",
    [
        (env.LITELLM_DB_NAME, f"db_{env.LITELLM_DB_NAME}"),
        (env.APP_ATTEST_DB_NAME, f"db_{env.APP_ATTEST_DB_NAME}"),
        ("arbitrary-db-name", "db_arbitrary-db-name"),
    ],
)
@pytest.mark.parametrize(
    "error,outcome",
    [
        (None, "success"),
        (OSError("refused"), "failure"),
        (TimeoutError(), "timeout"),
        (asyncio.CancelledError(), None),
    ],
)
async def test_pg_physical_connection(
    metrics_spy, mocker, db, dependency, error, outcome
):
    conn = MagicMock()
    connect = mocker.patch(
        "mlpa.core.services.metrics.asyncpg.connect",
        new=AsyncMock(return_value=conn, side_effect=error),
    )
    service = PGService(db)
    if error:
        with pytest.raises(type(error)):
            await service._metrics.connect("dsn", timeout=1)
    else:
        assert await service._metrics.connect("dsn", timeout=1) is conn
        conn.add_query_logger.assert_called_once_with(service._metrics.record_query)
    connect.assert_awaited_once_with("dsn", timeout=1)
    for candidate in ("success", "failure", "timeout"):
        assert metrics_spy.value(
            "dep_connections_total",
            dependency=dependency,
            stage="connection",
            outcome=candidate,
        ) == int(candidate == outcome)


@pytest.mark.parametrize(
    "error,outcome",
    [
        (None, "success"),
        (OSError("refused"), "failure"),
        (TimeoutError(), "timeout"),
    ],
)
async def test_pg_pool_start_outcomes(metrics_spy, mocker, error, outcome):
    pool = MagicMock()
    create = mocker.patch(
        "mlpa.core.services.pg_service.asyncpg.create_pool",
        new=AsyncMock(return_value=pool, side_effect=error),
    )
    service = PGService(env.APP_ATTEST_DB_NAME)
    if error:
        with pytest.raises(SystemExit):
            await service.connect()
        assert not service.connected
    else:
        await service.connect()
        assert service.connected
        assert create.call_args.kwargs["connect"] == service._metrics.connect
    assert (
        metrics_spy.value(
            "dep_connections_total",
            dependency=f"db_{env.APP_ATTEST_DB_NAME}",
            stage="pool_start",
            outcome=outcome,
        )
        == 1
    )
    # Pool lifecycle events must not inflate the physical connection denominator.
    assert (
        metrics_spy.value(
            "dep_connections_total",
            dependency=f"db_{env.APP_ATTEST_DB_NAME}",
            stage="connection",
            outcome=outcome,
        )
        == 0
    )


@pytest.mark.parametrize(
    "exception,kind",
    [
        (TimeoutError(), "client"),
        (
            asyncpg.QueryCanceledError("canceling statement due to statement timeout"),
            "statement",
        ),
        (
            asyncpg.LockNotAvailableError("canceling statement due to lock timeout"),
            "lock",
        ),
        (asyncpg.QueryCanceledError("canceling statement due to user request"), None),
        (asyncio.CancelledError(), None),
        (OSError(), None),
        (None, None),
    ],
)
def test_pg_query_timeout_classification(metrics_spy, exception, kind):
    service = PGService(env.LITELLM_DB_NAME)
    service._metrics.record_query(SimpleNamespace(exception=exception))
    for candidate in ("client", "statement", "lock"):
        assert metrics_spy.value(
            "dep_timeouts_total", dependency=f"db_{env.LITELLM_DB_NAME}", kind=candidate
        ) == int(candidate == kind)
    expected = (
        None
        if isinstance(exception, asyncio.CancelledError)
        else "timeout"
        if kind is not None
        else "success"
        if exception is None
        else "failure"
    )
    for outcome in ("success", "failure", "timeout"):
        assert metrics_spy.value(
            "dep_operations_total",
            dependency=f"db_{env.LITELLM_DB_NAME}",
            outcome=outcome,
        ) == int(outcome == expected)
    assert (
        metrics_spy.value(
            "dep_connections_total",
            dependency=f"db_{env.LITELLM_DB_NAME}",
            stage="connection",
            outcome="failure",
        )
        == 0
    )


@pytest.mark.parametrize(
    "error,outcome",
    [
        (None, "success"),
        (AuthenticationError(), "failure"),
        (RedisTimeoutError(), "timeout"),
        (TimeoutError(), "timeout"),
        (asyncio.CancelledError(), None),
    ],
)
async def test_redis_connection_outcomes(metrics_spy, mocker, error, outcome):
    mocker.patch.object(
        aioredis.Connection, "connect_check_health", new=AsyncMock(side_effect=error)
    )
    conn = MonitoredRedisConnection()
    if error:
        with pytest.raises(type(error)):
            await conn.connect_check_health()
    else:
        await conn.connect_check_health()
    for candidate in ("success", "failure", "timeout"):
        assert metrics_spy.value(
            "dep_connections_total",
            dependency="redis",
            stage="connection",
            outcome=candidate,
        ) == int(candidate == outcome)


async def test_redis_reuse_excluded(metrics_spy, mocker):
    conn = MonitoredRedisConnection()
    mocker.patch.object(
        aioredis.Connection,
        "is_connected",
        new_callable=lambda: property(lambda self: True),
    )
    mocker.patch.object(aioredis.Connection, "connect_check_health", new=AsyncMock())
    await conn.connect_check_health()
    assert (
        metrics_spy.value(
            "dep_connections_total",
            dependency="redis",
            stage="connection",
            outcome="success",
        )
        == 0
    )


async def test_redis_exhaustion_is_not_a_connection_failure(metrics_spy):
    pool = MonitoredRedisPool(max_connections=1)
    # Real pool allocation/rejection, with no socket opened.
    pool.get_available_connection()
    with pytest.raises(MaxConnectionsError):
        await pool.get_connection()
    assert metrics_spy.value("dep_pool_exhaustions_total", dependency="redis") == 1
    assert (
        metrics_spy.value(
            "dep_connections_total",
            dependency="redis",
            stage="connection",
            outcome="failure",
        )
        == 0
    )


@pytest.mark.parametrize("error", [RedisTimeoutError(), TimeoutError()])
async def test_redis_operation_timeout(metrics_spy, mocker, error):
    mocker.patch.object(
        aioredis.Redis, "_send_command_parse_response", new=AsyncMock(side_effect=error)
    )
    client = MonitoredRedis()
    with pytest.raises(type(error)):
        await client._send_command_parse_response(None, "GET", "private-key")
    assert (
        metrics_spy.value("dep_timeouts_total", dependency="redis", kind="operation")
        == 1
    )
    assert (
        metrics_spy.value("dep_operations_total", dependency="redis", outcome="timeout")
        == 1
    )
    assert (
        metrics_spy.value("dep_operations_total", dependency="redis", outcome="failure")
        == 0
    )
    assert (
        metrics_spy.value(
            "dep_connections_total",
            dependency="redis",
            stage="connection",
            outcome="timeout",
        )
        == 0
    )
    await client.aclose()


@pytest.mark.parametrize(
    "error,outcome",
    [
        (None, "success"),
        (OSError("connection lost"), "failure"),
        (asyncio.CancelledError(), None),
    ],
)
async def test_redis_operation_outcomes(metrics_spy, mocker, error, outcome):
    mocker.patch.object(
        aioredis.Redis,
        "_send_command_parse_response",
        new=AsyncMock(return_value=None, side_effect=error),
    )
    client = MonitoredRedis()
    try:
        if error is not None:
            with pytest.raises(type(error)):
                await client._send_command_parse_response(None, "GET", "private-key")
        else:
            # A cache miss (nil) is a successfully executed command.
            assert (
                await client._send_command_parse_response(None, "GET", "private-key")
                is None
            )
        for candidate in ("success", "failure", "timeout"):
            assert metrics_spy.value(
                "dep_operations_total", dependency="redis", outcome=candidate
            ) == int(candidate == outcome)
    finally:
        await client.aclose()


async def test_redis_retry_counts_each_attempt(metrics_spy, mocker):
    client = MonitoredRedis()
    conn = MagicMock()
    conn.retry = Retry(NoBackoff(), retries=1)
    conn.send_command = AsyncMock()
    conn.disconnect = AsyncMock()
    mocker.patch.object(
        client.connection_pool, "get_connection", new=AsyncMock(return_value=conn)
    )
    mocker.patch.object(client.connection_pool, "release", new=AsyncMock())
    mocker.patch.object(
        client,
        "parse_response",
        new=AsyncMock(side_effect=[RedisTimeoutError(), "value"]),
    )
    # Exercise the real redis-py retry loop and our per-attempt command hook.
    assert await client.get("private-key") == "value"
    assert conn.send_command.await_count == 2
    assert (
        metrics_spy.value("dep_operations_total", dependency="redis", outcome="timeout")
        == 1
    )
    assert (
        metrics_spy.value("dep_operations_total", dependency="redis", outcome="success")
        == 1
    )
    assert (
        metrics_spy.value("dep_operations_total", dependency="redis", outcome="failure")
        == 0
    )
    await client.aclose()


def test_postgres_expected_constraint_error_is_an_execution_failure(metrics_spy):
    PGService(env.APP_ATTEST_DB_NAME)._metrics.record_query(
        SimpleNamespace(exception=asyncpg.UniqueViolationError("duplicate key"))
    )
    assert (
        metrics_spy.value(
            "dep_operations_total",
            dependency=f"db_{env.APP_ATTEST_DB_NAME}",
            outcome="failure",
        )
        == 1
    )
    assert (
        metrics_spy.value(
            "dep_operations_total",
            dependency=f"db_{env.LITELLM_DB_NAME}",
            outcome="failure",
        )
        == 0
    )


async def test_redis_service_uses_instrumented_pool(metrics_spy, mocker):
    mocker.patch.object(MonitoredRedis, "ping", new=AsyncMock(return_value=True))
    service = RedisService()
    await service.connect()
    assert isinstance(service.client, MonitoredRedis)
    assert isinstance(service.client.connection_pool, MonitoredRedisPool)
    assert service.client.connection_pool.connection_class is MonitoredRedisConnection
    await service.close()


def test_dashboard_keeps_dependencies_separate():
    root = Path(__file__).resolve().parents[3]
    dashboard = json.loads(
        (root / "monitoring/dependency-connections.json").read_text()
    )
    assert "PROPOSED" in dashboard["title"]
    for dependency in ("litellm_v2", "appattest", "redis"):
        panels = [
            p for p in dashboard["panels"] if p["title"].startswith(dependency + " · ")
        ]
        assert len(panels) == 7
        for panel in panels:
            expr = panel["targets"][0]["expr"]
            assert f'dependency="{dependency}"' in expr
            assert 'namespace="llm-proxy-prod"' in expr
        ratio, budget = [p["targets"][0]["expr"] for p in panels[:2]]
        assert "[28d]" in ratio and "> 0" in ratio
        assert "0.001" in budget and "failure|timeout" in budget
        assert 'stage="connection"' in ratio
        operation_ratio = panels[-2]["targets"][0]["expr"]
        assert "mlpa_dep_operations_total" in operation_ratio
        assert "[28d]" in operation_ratio and "> 0" in operation_ratio
        assert "0.001" not in operation_ratio
        assert "stage=" not in operation_ratio
