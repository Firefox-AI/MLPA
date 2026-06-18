import contextlib

import httpx
import pytest
from loguru import logger as loguru_logger

from mlpa.core.config import env
from mlpa.core.logger import _enable_httpx_logging


@contextlib.contextmanager
def _capture_logs():
    records = []
    sink_id = loguru_logger.add(records.append, level="DEBUG", format="{message}")
    try:
        yield records
    finally:
        loguru_logger.remove(sink_id)


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
