import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

from mlpa.core.completions import get_completion, stream_completion
from mlpa.core.config import Env
from tests.consts import SAMPLE_REQUEST, SUCCESSFUL_CHAT_RESPONSE


def test_timeout_config_default_values():
    """Test that timeout configuration has correct default values."""
    env = Env()

    assert env.STREAMING_TIMEOUT_SECONDS == 300
    assert env.UPSTREAM_TIMEOUT_SECONDS == 30


def test_timeout_config_from_env():
    """Test that timeout configuration can be overridden via environment variables."""
    env_vars = {
        "STREAMING_TIMEOUT_SECONDS": "600",
        "UPSTREAM_TIMEOUT_SECONDS": "60",
    }

    with patch.dict(os.environ, env_vars):
        env = Env()

        assert env.STREAMING_TIMEOUT_SECONDS == 600
        assert env.UPSTREAM_TIMEOUT_SECONDS == 60


async def test_get_completion_uses_configurable_timeout(mocker):
    """Test that get_completion uses the configurable timeout from env."""
    mock_response = MagicMock()
    mock_response.json.return_value = SUCCESSFUL_CHAT_RESPONSE
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    mock_async_client_class = mocker.patch("mlpa.core.completions.httpx.AsyncClient")
    mock_async_client_class.return_value.__aenter__.return_value = mock_client

    mocker.patch("mlpa.core.completions.metrics")

    custom_timeout = 45
    mock_env = mocker.patch("mlpa.core.completions.env")
    mock_env.UPSTREAM_TIMEOUT_SECONDS = custom_timeout

    await get_completion(SAMPLE_REQUEST)

    mock_client.post.assert_awaited_once()
    _, call_kwargs = mock_client.post.call_args
    assert call_kwargs.get("timeout") == custom_timeout


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


async def test_get_completion_uses_default_timeout(mocker):
    """Test that get_completion uses default timeout when not configured."""
    mock_response = MagicMock()
    mock_response.json.return_value = SUCCESSFUL_CHAT_RESPONSE
    mock_response.raise_for_status.return_value = None

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_response

    mock_async_client_class = mocker.patch("mlpa.core.completions.httpx.AsyncClient")
    mock_async_client_class.return_value.__aenter__.return_value = mock_client

    mocker.patch("mlpa.core.completions.metrics")

    await get_completion(SAMPLE_REQUEST)

    mock_client.post.assert_awaited_once()
    _, call_kwargs = mock_client.post.call_args
    assert call_kwargs.get("timeout") == 30


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
