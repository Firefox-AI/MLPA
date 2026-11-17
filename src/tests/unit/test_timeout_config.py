import os
from unittest.mock import patch

from mlpa.core import http_client
from mlpa.core.completions import stream_completion
from mlpa.core.config import Env
from tests.consts import SAMPLE_REQUEST


def test_httpx_client_uses_configurable_timeouts_and_limits(mocker):
    """Test that shared httpx client uses configured timeout and pool limits."""
    mocker.patch.object(http_client, "_client", None)
    mock_env = mocker.patch("mlpa.core.http_client.env")
    mock_env.HTTPX_CONNECT_TIMEOUT_SECONDS = 5
    mock_env.HTTPX_READ_TIMEOUT_SECONDS = 15
    mock_env.HTTPX_WRITE_TIMEOUT_SECONDS = 25
    mock_env.HTTPX_POOL_TIMEOUT_SECONDS = 35
    mock_env.HTTPX_MAX_CONNECTIONS = 150
    mock_env.HTTPX_MAX_KEEPALIVE_CONNECTIONS = 75
    mock_env.HTTPX_KEEPALIVE_EXPIRY_SECONDS = 12

    mock_async_client = mocker.patch("mlpa.core.http_client.httpx.AsyncClient")

    http_client.get_http_client()

    _, call_kwargs = mock_async_client.call_args
    timeout = call_kwargs["timeout"]
    limits = call_kwargs["limits"]
    assert timeout.connect == 5
    assert timeout.read == 15
    assert timeout.write == 25
    assert timeout.pool == 35
    assert limits.max_connections == 150
    assert limits.max_keepalive_connections == 75
    assert limits.keepalive_expiry == 12
    http_client._client = None
