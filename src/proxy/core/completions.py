import time

import httpx
import tiktoken
from fastapi import HTTPException

from .classes import AuthorizedChatRequest
from .config import LITELLM_COMPLETIONS_URL, LITELLM_HEADERS
from .prometheus_metrics import PrometheusResult, metrics


async def stream_completion(authorized_chat_request: AuthorizedChatRequest):
	"""
	Proxies a streaming request to LiteLLM.
	Yields response chunks as they are received and logs metrics.
	"""
	start_time = time.time()
	body = {
		"model": authorized_chat_request.model,
		"messages": authorized_chat_request.messages,
		"temperature": authorized_chat_request.temperature,
		"top_p": authorized_chat_request.top_p,
		"max_tokens": authorized_chat_request.max_completion_tokens,
		"user": authorized_chat_request.user,
		"stream": True,
	}
	result = PrometheusResult.ERROR
	is_first_token = True
	num_completion_tokens = 0
	try:
		async with httpx.AsyncClient() as client:
			async with client.stream(
				"POST",
				LITELLM_COMPLETIONS_URL,
				headers=LITELLM_HEADERS,
				json=body,
				timeout=30,
			) as response:
				response.raise_for_status()
				async for chunk in response.aiter_bytes():
					num_completion_tokens += 1
					if is_first_token:
						metrics.chat_completion_ttft.observe(time.time() - start_time)
						is_first_token = False
					yield chunk

				# Update token metrics after streaming is complete
				try:
					tokenizer = tiktoken.encoding_for_model(
						authorized_chat_request.model
					)
				except KeyError:
					tokenizer = tiktoken.get_encoding("cl100k_base")
				prompt_tokens = len(
					tokenizer.encode(
						message["content"]
						for message in authorized_chat_request.messages
					)
				)
				metrics.chat_tokens.labels(type="prompt").inc(prompt_tokens)
				metrics.chat_tokens.labels(type="completion").inc(num_completion_tokens)
				result = PrometheusResult.SUCCESS
	except httpx.HTTPStatusError as e:
		print(
			f"Upstream service returned an error: {e.response.status_code} - {e.response.text}"
		)
		return
	except Exception as e:
		print(f"Failed to proxy request to {LITELLM_COMPLETIONS_URL}: {e}")
		return
	finally:
		metrics.chat_completion_latency.labels(result=result).observe(
			time.time() - start_time
		)


async def get_completion(authorized_chat_request: AuthorizedChatRequest):
	"""
	Proxies a non-streaming request to LiteLLM.
	"""
	start_time = time.time()
	body = {
		"model": authorized_chat_request.model,
		"messages": authorized_chat_request.messages,
		"temperature": authorized_chat_request.temperature,
		"top_p": authorized_chat_request.top_p,
		"max_tokens": authorized_chat_request.max_completion_tokens,
		"user": authorized_chat_request.user,
		"stream": False,
	}
	result = PrometheusResult.ERROR
	try:
		async with httpx.AsyncClient() as client:
			response = await client.post(
				LITELLM_COMPLETIONS_URL, headers=LITELLM_HEADERS, json=body, timeout=10
			)
			response.raise_for_status()
			data = response.json()
			usage = data.get("usage", {})
			prompt_tokens = usage.get("prompt_tokens", 0)
			completion_tokens = usage.get("completion_tokens", 0)

			metrics.chat_tokens.labels(type="prompt").inc(prompt_tokens)
			metrics.chat_tokens.labels(type="completion").inc(completion_tokens)

			result = PrometheusResult.SUCCESS
			return data
	except Exception as e:
		raise HTTPException(
			status_code=500,
			detail={
				"error": f"Failed to proxy request to {LITELLM_COMPLETIONS_URL}: {e}"
			},
		)
	finally:
		metrics.chat_completion_latency.labels(result=result).observe(
			time.time() - start_time
		)
