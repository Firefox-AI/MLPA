import os
from unittest.mock import MagicMock, patch

from mlpa.core import http_client
from mlpa.core.completions import stream_completion
from mlpa.core.config import Env
from tests.consts import SAMPLE_REQUEST


def test_timeout_config_default_values():
    """Test that timeout configuration has correct default values."""
    env = Env()

    assert env.STREAMING_TIMEOUT_SECONDS == 300
    assert env.HTTPX_CONNECT_TIMEOUT_SECONDS == 30
    assert env.HTTPX_READ_TIMEOUT_SECONDS == 30
    assert env.HTTPX_WRITE_TIMEOUT_SECONDS == 30
    assert env.HTTPX_POOL_TIMEOUT_SECONDS == 30
    assert env.HTTPX_MAX_CONNECTIONS == 100
    assert env.HTTPX_MAX_KEEPALIVE_CONNECTIONS == 20
    assert env.HTTPX_KEEPALIVE_EXPIRY_SECONDS == 15


def test_timeout_config_from_env():
    """Test that timeout configuration can be overridden via environment variables."""
    env_vars = {
        "STREAMING_TIMEOUT_SECONDS": "600",
        "HTTPX_CONNECT_TIMEOUT_SECONDS": "10",
        "HTTPX_READ_TIMEOUT_SECONDS": "20",
        "HTTPX_WRITE_TIMEOUT_SECONDS": "30",
        "HTTPX_POOL_TIMEOUT_SECONDS": "40",
        "HTTPX_MAX_CONNECTIONS": "200",
        "HTTPX_MAX_KEEPALIVE_CONNECTIONS": "50",
        "HTTPX_KEEPALIVE_EXPIRY_SECONDS": "25",
    }

    with patch.dict(os.environ, env_vars):
        env = Env()

        assert env.STREAMING_TIMEOUT_SECONDS == 600
        assert env.HTTPX_CONNECT_TIMEOUT_SECONDS == 10
        assert env.HTTPX_READ_TIMEOUT_SECONDS == 20
        assert env.HTTPX_WRITE_TIMEOUT_SECONDS == 30
        assert env.HTTPX_POOL_TIMEOUT_SECONDS == 40
        assert env.HTTPX_MAX_CONNECTIONS == 200
        assert env.HTTPX_MAX_KEEPALIVE_CONNECTIONS == 50
        assert env.HTTPX_KEEPALIVE_EXPIRY_SECONDS == 25


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


async def test_stream_completion_uses_configurable_timeout(httpx_mock, mocker):
    """Test that stream_completion uses the configurable timeout from env."""
    from pytest_httpx import IteratorStream

    mock_chunks = [b"data: chunk1", b"data: [DONE]"]
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:4000/v1/chat/completions",
        stream=IteratorStream(mock_chunks),
        status_code=200,
    )

    mocker.patch("mlpa.core.completions.metrics")
    mock_tokenizer = MagicMock()
    mock_tokenizer.encode.return_value = [1, 2]
    mock_tiktoken = mocker.patch("mlpa.core.completions.tiktoken")
    mock_tiktoken.encoding_for_model.return_value = mock_tokenizer
    mock_tiktoken.get_encoding.return_value = mock_tokenizer

    custom_timeout = 600
    mock_env = mocker.patch("mlpa.core.completions.env")
    mock_env.STREAMING_TIMEOUT_SECONDS = custom_timeout

    async for _ in stream_completion(SAMPLE_REQUEST):
        pass

    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert mock_env.STREAMING_TIMEOUT_SECONDS == custom_timeout


async def test_stream_completion_uses_default_timeout(httpx_mock, mocker):
    """Test that stream_completion uses default timeout when not configured."""
    from pytest_httpx import IteratorStream

    mock_chunks = [b"data: chunk1", b"data: [DONE]"]
    httpx_mock.add_response(
        method="POST",
        url="http://localhost:4000/v1/chat/completions",
        stream=IteratorStream(mock_chunks),
        status_code=200,
    )

    mocker.patch("mlpa.core.completions.metrics")
    mock_tokenizer = MagicMock()
    mock_tokenizer.encode.return_value = [1, 2]
    mock_tiktoken = mocker.patch("mlpa.core.completions.tiktoken")
    mock_tiktoken.encoding_for_model.return_value = mock_tokenizer
    mock_tiktoken.get_encoding.return_value = mock_tokenizer

    async for _ in stream_completion(SAMPLE_REQUEST):
        pass

    from mlpa.core.completions import env

    assert env.STREAMING_TIMEOUT_SECONDS == 300
