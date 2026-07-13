import contextlib
import sys

import asyncpg
import httpx
import pytest
from loguru import logger as loguru_logger

from mlpa.core import logger as logger_module
from mlpa.core.config import env
from mlpa.core.logger import _enable_asyncpg_logging, _enable_httpx_logging


@contextlib.contextmanager
def _capture_logs():
    records = []
    sink_id = loguru_logger.add(records.append, level="DEBUG", format="{message}")
    try:
        yield records
    finally:
        loguru_logger.remove(sink_id)


@contextlib.contextmanager
def _capture_only_logs_at_level(level):
    records = []
    loguru_logger.remove()
    sink_id = loguru_logger.add(records.append, level=level, format="{message}")
    try:
        yield records
    finally:
        loguru_logger.remove(sink_id)
        loguru_logger.add(sys.stderr, level="DEBUG")


async def test_httpx_wrapper_logs_exc_type_on_transport_failure(mocker):
    """The HTTPX logging wrapper must name the exception type + repr on failure.

    Transport errors often stringify to ``""``, so the bare URL alone (the old
    log) was undiagnosable. This is the first line of the 502 "triple".
    """
    mocker.patch.object(env, "HTTPX_LOGGING", True)

    # Save and restore the real httpx methods so the global patch never leaks
    # into other tests, regardless of whether logging was already enabled.
    before_get = httpx.AsyncClient.get
    before_post = httpx.AsyncClient.post
    try:
        _enable_httpx_logging()

        def _raise(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("", request=request)

        transport = httpx.MockTransport(_raise)
        url = "http://litellm:8000/v1/chat/completions"
        with _capture_logs() as records:
            async with httpx.AsyncClient(transport=transport) as client:
                with pytest.raises(httpx.ConnectError):
                    await client.post(url)
    finally:
        httpx.AsyncClient.get = before_get
        httpx.AsyncClient.post = before_post

    failures = [
        item.record["message"]
        for item in records
        if item.record["level"].name == "ERROR"
        and "request failed" in item.record["message"]
    ]
    assert len(failures) == 1
    msg = failures[0]
    assert "ConnectError" in msg
    assert url in msg


@pytest.mark.parametrize(
    ("loguru_level", "expected_truncate_calls"),
    [
        ("INFO", 0),
        ("DEBUG", 2),
    ],
)
async def test_httpx_debug_payload_build_follows_loguru_level(
    mocker, loguru_level, expected_truncate_calls
):
    mocker.patch.object(env, "HTTPX_LOGGING", True)
    truncate_mapping = mocker.spy(logger_module, "_truncate_mapping")

    before_get = httpx.AsyncClient.get
    before_post = httpx.AsyncClient.post
    try:
        _enable_httpx_logging()

        transport = httpx.MockTransport(lambda request: httpx.Response(200))
        url = "http://litellm:8000/v1/chat/completions"
        with _capture_only_logs_at_level(loguru_level) as records:
            async with httpx.AsyncClient(transport=transport) as client:
                await client.post(
                    url,
                    params={"user_id": "test-user"},
                    json={
                        "messages": [{"role": "user", "content": "secret"}],
                        "model": "exa",
                    },
                )
    finally:
        httpx.AsyncClient.get = before_get
        httpx.AsyncClient.post = before_post

    assert truncate_mapping.call_count == expected_truncate_calls
    request_logs = [
        item.record["message"]
        for item in records
        if "HTTPX POST request ->" in item.record["message"]
    ]
    if loguru_level == "DEBUG":
        assert len(request_logs) == 1
        assert "test-user" in request_logs[0]
    else:
        assert request_logs == []


@pytest.mark.parametrize(
    ("loguru_level", "expect_debug_logs"),
    [
        ("INFO", False),
        ("DEBUG", True),
    ],
)
async def test_asyncpg_debug_args_build_follows_loguru_level(
    mocker, loguru_level, expect_debug_logs
):
    mocker.patch.object(env, "ASYNCPG_LOGGING", True)
    repr_calls = 0

    class TrackedArg:
        def __repr__(self):
            nonlocal repr_calls
            repr_calls += 1
            return "TrackedArg('test-user')"

    async def _execute(self, query, *args, **kwargs):
        return "OK"

    before_execute = asyncpg.connection.Connection.execute
    try:
        asyncpg.connection.Connection.execute = _execute
        _enable_asyncpg_logging()

        with _capture_only_logs_at_level(loguru_level) as records:
            result = await asyncpg.connection.Connection.execute(
                object(), "SELECT $1", TrackedArg()
            )
    finally:
        asyncpg.connection.Connection.execute = before_execute

    assert result == "OK"
    debug_logs = [
        item.record["message"]
        for item in records
        if "ASYNCPG execute" in item.record["message"]
    ]
    if expect_debug_logs:
        assert len(debug_logs) == 2
        assert repr_calls >= 2
        assert all("TrackedArg('test-user')" in message for message in debug_logs)
    else:
        assert debug_logs == []
        assert repr_calls == 0


async def test_asyncpg_wrapper_logs_query_on_execute_failure(mocker):
    mocker.patch.object(env, "ASYNCPG_LOGGING", True)

    async def _execute(self, query, *args, **kwargs):
        raise RuntimeError("bad query")

    before_execute = asyncpg.connection.Connection.execute
    try:
        asyncpg.connection.Connection.execute = _execute
        _enable_asyncpg_logging()

        query = "SELECT bad"
        with _capture_logs() as records:
            with pytest.raises(RuntimeError):
                await asyncpg.connection.Connection.execute(object(), query)
    finally:
        asyncpg.connection.Connection.execute = before_execute

    failures = [
        item.record["message"]
        for item in records
        if item.record["level"].name == "ERROR"
        and "ASYNCPG execute failed" in item.record["message"]
    ]
    assert failures == [f"ASYNCPG execute failed -> query='{query}'"]
