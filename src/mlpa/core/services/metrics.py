"""Dependency instrumentation for Postgres and the pinned redis-py APIs."""

import asyncio

import asyncpg
import redis.asyncio as aioredis
from redis.exceptions import MaxConnectionsError
from redis.exceptions import TimeoutError as RedisTimeoutError

from mlpa.core.prometheus_metrics import metrics


class PGMetrics:
    """Record Postgres connection, pool startup, and query outcomes."""

    def __init__(self, dependency: str):
        self.dependency = dependency
        for outcome in ("success", "failure", "timeout"):
            metrics.dep_operations_total.labels(
                dependency=self.dependency, outcome=outcome
            )
        for stage in ("connection", "pool_start"):
            for outcome in ("success", "failure", "timeout"):
                metrics.dep_connections_total.labels(
                    dependency=self.dependency, stage=stage, outcome=outcome
                )

    async def connect(self, *args, **kwargs):
        """Count actual new sessions, including pool growth and reconnections."""
        outcome = "failure"
        try:
            conn = await asyncpg.connect(*args, **kwargs)
            conn.add_query_logger(self.record_query)
            outcome = "success"
            return conn
        except TimeoutError:
            outcome = "timeout"
            raise
        except asyncio.CancelledError:
            outcome = None
            raise
        finally:
            if outcome is not None:
                metrics.dep_connections_total.labels(
                    dependency=self.dependency, stage="connection", outcome=outcome
                ).inc()

    def record_query(self, record):
        exc = record.exception
        if isinstance(exc, asyncio.CancelledError):
            return
        kind = None
        if isinstance(exc, TimeoutError):
            kind = "client"
        elif isinstance(exc, asyncpg.QueryCanceledError) and "statement timeout" in str(
            exc
        ):
            kind = "statement"
        elif isinstance(exc, asyncpg.LockNotAvailableError) and "lock timeout" in str(
            exc
        ):
            kind = "lock"
        outcome = (
            "timeout" if kind is not None else "success" if exc is None else "failure"
        )
        metrics.dep_operations_total.labels(
            dependency=self.dependency, outcome=outcome
        ).inc()
        if kind is not None:
            metrics.dep_timeouts_total.labels(
                dependency=self.dependency, kind=kind
            ).inc()

    def record_pool_start(self, error: Exception | None = None):
        outcome = (
            "success"
            if error is None
            else "timeout"
            if isinstance(error, TimeoutError)
            else "failure"
        )
        metrics.dep_connections_total.labels(
            dependency=self.dependency, stage="pool_start", outcome=outcome
        ).inc()


class MonitoredRedisConnection(aioredis.Connection):
    async def connect_check_health(self, check_health=True, retry_socket_connect=True):
        if self.is_connected:
            return await super().connect_check_health(
                check_health, retry_socket_connect
            )
        outcome = "failure"
        try:
            result = await super().connect_check_health(
                check_health, retry_socket_connect
            )
            outcome = "success"
            return result
        except (TimeoutError, RedisTimeoutError):
            outcome = "timeout"
            raise
        except asyncio.CancelledError:
            outcome = None
            raise
        finally:
            if outcome is not None:
                metrics.dep_connections_total.labels(
                    dependency="redis", stage="connection", outcome=outcome
                ).inc()


class MonitoredRedisPool(aioredis.ConnectionPool):
    async def get_connection(self, *args, **kwargs):
        try:
            return await super().get_connection(*args, **kwargs)
        except MaxConnectionsError:
            metrics.dep_pool_exhaustions_total.labels(dependency="redis").inc()
            raise


class MonitoredRedis(aioredis.Redis):
    @staticmethod
    def initialize_metrics():
        for outcome in ("success", "failure", "timeout"):
            metrics.dep_operations_total.labels(dependency="redis", outcome=outcome)
            metrics.dep_connections_total.labels(
                dependency="redis", stage="connection", outcome=outcome
            )
        metrics.dep_timeouts_total.labels(dependency="redis", kind="operation")
        metrics.dep_pool_exhaustions_total.labels(dependency="redis")

    async def _send_command_parse_response(self, conn, command_name, *args, **options):
        # Inside redis-py's retry loop: count each command attempt, including reuse.
        # Pipelines (including transactions) bypass this hook via send_packed_command
        # and are not counted; instrument that path if pipeline usage is added.
        outcome = None
        try:
            result = await super()._send_command_parse_response(
                conn, command_name, *args, **options
            )
        except (TimeoutError, RedisTimeoutError):
            outcome = "timeout"
            metrics.dep_timeouts_total.labels(
                dependency="redis", kind="operation"
            ).inc()
            raise
        except Exception:
            outcome = "failure"
            raise
        else:
            outcome = "success"
            return result
        finally:
            if outcome is not None:
                metrics.dep_operations_total.labels(
                    dependency="redis", outcome=outcome
                ).inc()
