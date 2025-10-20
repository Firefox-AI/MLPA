import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from pytest_httpx import HTTPXMock, IteratorStream

from proxy.core.completions import get_completion, stream_completion
from proxy.core.config import LITELLM_COMPLETIONS_URL
from proxy.core.prometheus_metrics import PrometheusResult
from tests.consts import SAMPLE_REQUEST, SUCCESSFUL_CHAT_RESPONSE


@pytest.mark.asyncio
async def test_get_completion_success(mocker):
	"""
	Tests the successful execution path of get_completion.
	- Verifies the external API call is made correctly.
	- Verifies metrics are incremented correctly.
	- Verifies the function returns the expected data.
	"""
	# Arrange: Mock all external dependencies
	mock_response = MagicMock()
	mock_response.json.return_value = SUCCESSFUL_CHAT_RESPONSE
	# Simulate a 2xx status by having raise_for_status do nothing
	mock_response.raise_for_status.return_value = None

	# This mock will be the 'client' inside the 'async with' block
	mock_client = AsyncMock()
	mock_client.post.return_value = mock_response

	# Patch the AsyncClient class where it's used
	mock_async_client_class = mocker.patch("proxy.core.completions.httpx.AsyncClient")
	# Configure the mock class's context manager to return our client
	mock_async_client_class.return_value.__aenter__.return_value = mock_client

	# Mock the metrics to check if they are called
	mock_metrics = mocker.patch("proxy.core.completions.metrics")

	# Act: Call the function under test
	result_data = await get_completion(SAMPLE_REQUEST)

	# Assert: Verify the behavior and outcome
	# 1. Check that the httpx client was used to make the correct call
	mock_client.post.assert_awaited_once()
	_, call_kwargs = mock_client.post.call_args
	sent_json = call_kwargs.get("json", {})
	assert sent_json["model"] == SAMPLE_REQUEST.model
	assert sent_json["messages"] == SAMPLE_REQUEST.messages
	assert sent_json["user"] == SAMPLE_REQUEST.user
	assert sent_json["stream"] == SAMPLE_REQUEST.stream

	# 2. Check that the token metrics were incremented correctly
	mock_metrics.chat_tokens.labels.assert_any_call(type="prompt")
	mock_metrics.chat_tokens.labels().inc.assert_any_call(
		SUCCESSFUL_CHAT_RESPONSE["usage"]["prompt_tokens"]
	)

	mock_metrics.chat_tokens.labels.assert_any_call(type="completion")
	mock_metrics.chat_tokens.labels().inc.assert_any_call(
		SUCCESSFUL_CHAT_RESPONSE["usage"]["completion_tokens"]
	)

	# 3. Check that the latency metric was observed with SUCCESS
	mock_metrics.chat_completion_latency.labels.assert_called_once_with(
		result=PrometheusResult.SUCCESS
	)
	mock_metrics.chat_completion_latency.labels().observe.assert_called_once()

	# 4. Check that the function returned the correct data
	assert result_data == SUCCESSFUL_CHAT_RESPONSE


@pytest.mark.asyncio
async def test_get_completion_http_error(mocker):
	"""
	Tests that an HTTPException is raised when the downstream API returns an error.
	"""
	# Arrange: Mock httpx to simulate an HTTP error (e.g., 400 or 500)
	mock_response = MagicMock()
	mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
		"Bad Request", request=MagicMock(), response=MagicMock()
	)

	mock_client = AsyncMock()
	mock_client.post.return_value = mock_response
	mock_async_client_class = mocker.patch("proxy.core.completions.httpx.AsyncClient")
	mock_async_client_class.return_value.__aenter__.return_value = mock_client

	mock_metrics = mocker.patch("proxy.core.completions.metrics")

	# Act & Assert: Expect an HTTPException to be raised
	with pytest.raises(HTTPException) as exc_info:
		await get_completion(SAMPLE_REQUEST)

	# 1. Verify exception details
	assert exc_info.value.status_code == 500
	assert "Failed to proxy request" in exc_info.value.detail["error"]

	# 2. Verify latency metric was observed with ERROR
	mock_metrics.chat_completion_latency.labels.assert_called_once_with(
		result=PrometheusResult.ERROR
	)
	mock_metrics.chat_completion_latency.labels().observe.assert_called_once()

	# 3. Verify token metrics were NOT called
	mock_metrics.chat_tokens.labels.assert_not_called()


@pytest.mark.asyncio
async def test_get_completion_network_error(mocker):
	"""
	Tests that an HTTPException is raised on a network-level error (e.g., timeout).
	"""
	# Arrange: Mock httpx to raise a network error
	mock_client = AsyncMock()
	mock_client.post.side_effect = httpx.TimeoutException("Connection timed out")
	mock_async_client_class = mocker.patch("proxy.core.completions.httpx.AsyncClient")
	mock_async_client_class.return_value.__aenter__.return_value = mock_client

	mock_metrics = mocker.patch("proxy.core.completions.metrics")

	# Act & Assert: Expect an HTTPException
	with pytest.raises(HTTPException) as exc_info:
		await get_completion(SAMPLE_REQUEST)

	# 1. Verify exception details
	assert exc_info.value.status_code == 500
	assert "Connection timed out" in exc_info.value.detail["error"]

	# 2. Verify latency metric was observed with ERROR
	mock_metrics.chat_completion_latency.labels.assert_called_once_with(
		result=PrometheusResult.ERROR
	)
	mock_metrics.chat_completion_latency.labels().observe.assert_called_once()


@pytest.mark.asyncio
async def test_stream_completion_success(httpx_mock: HTTPXMock, mocker):
	"""
	Tests the successful execution of a streaming request using pytest-httpx.
	- Verifies the yielded chunks are correct.
	- Verifies TTFT and other metrics are recorded correctly.
	"""
	# Arrange
	# 1. Create mock data for the stream
	mock_chunks = [b"data: chunk1", b"data: chunk2", b"data: [DONE]"]

	# 2. Use pytest-httpx to mock the response for the correct URL and method
	httpx_mock.add_response(
		method="POST",
		url=LITELLM_COMPLETIONS_URL,
		stream=IteratorStream(mock_chunks),
		status_code=200,
	)

	# 3. Mock metrics and tiktoken
	mock_metrics = mocker.patch("proxy.core.completions.metrics")
	mock_tokenizer = MagicMock()
	mock_tokenizer.encode.return_value = [1, 2]  # Simulate 2 prompt tokens
	mock_tiktoken = mocker.patch("proxy.core.completions.tiktoken")
	mock_tiktoken.encoding_for_model.return_value = mock_tokenizer
	mock_tiktoken.get_encoding.return_value = mock_tokenizer

	# Act: Consume the async generator
	received_chunks = [chunk async for chunk in stream_completion(SAMPLE_REQUEST)]

	# Assert
	# 1. Verify the received data matches the mocked stream
	assert received_chunks == mock_chunks

	# 2. Verify the request was made correctly
	request = httpx_mock.get_request()
	assert request is not None
	request_body = json.loads(request.content)
	assert request_body["stream"] is True
	assert request_body["user"] == "test-user-123"
	assert request_body["model"] == "test-model"

	# 3. Verify TTFT metric was observed
	mock_metrics.chat_completion_ttft.observe.assert_called_once()

	# 4. Verify token counts
	mock_metrics.chat_tokens.labels.assert_any_call(type="prompt")
	mock_metrics.chat_tokens.labels().inc.assert_any_call(2)
	mock_metrics.chat_tokens.labels.assert_any_call(type="completion")
	mock_metrics.chat_tokens.labels().inc.assert_any_call(len(mock_chunks))

	# 5. Verify final latency metric
	mock_metrics.chat_completion_latency.labels.assert_called_once_with(
		result=PrometheusResult.SUCCESS
	)
	mock_metrics.chat_completion_latency.labels().observe.assert_called_once()
