import json
import time

import httpx
import tiktoken
from fastapi import HTTPException
from loguru import logger

from mlpa.core.classes import AuthorizedChatRequest
from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    LITELLM_COMPLETION_AUTH_HEADERS,
    LITELLM_COMPLETIONS_URL,
)
from mlpa.core.prometheus_metrics import PrometheusResult, metrics
from mlpa.core.utils import is_rate_limit_error


async def _handle_rate_limit_error(e: httpx.HTTPStatusError, user: str) -> None:
    metrics.auth_throttled_requests_total.inc()

    try:
        error_text = e.response.text
        if error_text:
            error_data = json.loads(error_text)
            if is_rate_limit_error(error_data, ["budget"]):
                metrics.auth_rate_limit_dropped_total.inc()
                logger.warning(f"Budget limit exceeded for user {user}: {error_text}")
                raise HTTPException(
                    status_code=429,
                    detail={"error": ERROR_CODE_BUDGET_LIMIT_EXCEEDED},
                    headers={"Retry-After": "86400"},
                )
            elif is_rate_limit_error(error_data, ["rate"]):
                metrics.auth_rate_limit_dropped_total.inc()
                logger.warning(f"Rate limit exceeded for user {user}: {error_text}")
                raise HTTPException(
                    status_code=429,
                    detail={"error": ERROR_CODE_RATE_LIMIT_EXCEEDED},
                    headers={"Retry-After": "60"},
                )
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        pass


async def stream_completion(authorized_chat_request: AuthorizedChatRequest):
    start_time = time.time()
    model = authorized_chat_request.model
    body = {
        "model": model,
        "messages": authorized_chat_request.messages,
        "temperature": authorized_chat_request.temperature,
        "top_p": authorized_chat_request.top_p,
        "max_tokens": authorized_chat_request.max_completion_tokens,
        "user": authorized_chat_request.user,
        "stream": True,
    }

    metrics.router_requests_total.labels(model_name=model).inc()
    metrics.ai_backend_requests_total.labels(model_name=model).inc()

    result = PrometheusResult.ERROR
    is_first_token = True
    num_completion_tokens = 0
    streaming_started = False
    logger.debug(
        f"Starting a stream completion using {authorized_chat_request.model}, for user {authorized_chat_request.user}",
    )
    try:
        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                LITELLM_COMPLETIONS_URL,
                headers=LITELLM_COMPLETION_AUTH_HEADERS,
                json=body,
                timeout=30,
            ) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    if e.response.status_code == 408:
                        metrics.ai_backend_timeouts_total.labels(model_name=model).inc()
                    raise e

                async for chunk in response.aiter_bytes():
                    num_completion_tokens += 1
                    if is_first_token:
                        metrics.chat_completion_ttft.observe(time.time() - start_time)
                        is_first_token = False
                        streaming_started = True
                    yield chunk

                # TODO: The tokenizer probably should be initialized once at startup and cached.
                # TODO: Once we will start using model's aliases, the try will always fail. So we will need to use the universal encoding.
                try:
                    tokenizer = tiktoken.encoding_for_model(model)
                except KeyError:
                    tokenizer = tiktoken.get_encoding("cl100k_base")
                prompt_text = "".join(
                    message["content"] for message in authorized_chat_request.messages
                )
                prompt_tokens = len(tokenizer.encode(prompt_text))
                metrics.chat_tokens.labels(type="prompt").inc(prompt_tokens)
                metrics.chat_tokens.labels(type="completion").inc(num_completion_tokens)

                metrics.ai_prompt_tokens_total.labels(model_name=model).inc(
                    prompt_tokens
                )
                metrics.ai_completion_tokens_total.labels(model_name=model).inc(
                    num_completion_tokens
                )
                metrics.ai_total_tokens_total.labels(model_name=model).inc(
                    prompt_tokens + num_completion_tokens
                )

                result = PrometheusResult.SUCCESS
    except httpx.HTTPStatusError as e:
        metrics.error_count_total.labels(
            error_type=f"HTTP_{e.response.status_code}"
        ).inc()
        if not streaming_started:
            yield f'data: {{"error": "Upstream service returned an error"}}\n\n'.encode()
    except Exception as e:
        metrics.error_count_total.labels(error_type=type(e).__name__).inc()
        if not streaming_started:
            yield f'data: {{"error": "Failed to proxy request"}}\n\n'.encode()
    finally:
        duration = time.time() - start_time
        metrics.chat_completion_latency.labels(result=result).observe(duration)
        metrics.ai_backend_request_duration_seconds.labels(model_name=model).observe(
            duration
        )


async def get_completion(authorized_chat_request: AuthorizedChatRequest):
    """
    Proxies a non-streaming request to LiteLLM.
    """
    start_time = time.time()
    model = authorized_chat_request.model
    body = {
        "model": model,
        "messages": authorized_chat_request.messages,
        "temperature": authorized_chat_request.temperature,
        "top_p": authorized_chat_request.top_p,
        "max_tokens": authorized_chat_request.max_completion_tokens,
        "user": authorized_chat_request.user,
        "mock_response": authorized_chat_request.mock_response,
        "stream": False,
    }
    result = PrometheusResult.ERROR
    logger.debug(
        f"Starting a non-stream completion using {authorized_chat_request.model}, for user {authorized_chat_request.user}",
    )

    metrics.router_requests_total.labels(model_name=model).inc()
    metrics.ai_backend_requests_total.labels(model_name=model).inc()

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                LITELLM_COMPLETIONS_URL,
                headers=LITELLM_COMPLETION_AUTH_HEADERS,
                json=body,
                timeout=10,
            )
            data = response.json()
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 or e.response.status_code == 400:
                    await _handle_rate_limit_error(e, authorized_chat_request.user)
                elif e.response.status_code == 408:
                    metrics.ai_backend_timeouts_total.labels(model_name=model).inc()
                metrics.error_count_total.labels(
                    error_type=f"HTTP_{e.response.status_code}"
                ).inc()
                logger.error(
                    f"Upstream service returned an error: {e.response.status_code} - {e.response.text}"
                )
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=f"Upstream service returned an error",
                )
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            metrics.chat_tokens.labels(type="prompt").inc(prompt_tokens)
            metrics.chat_tokens.labels(type="completion").inc(completion_tokens)

            metrics.ai_prompt_tokens_total.labels(model_name=model).inc(prompt_tokens)
            metrics.ai_completion_tokens_total.labels(model_name=model).inc(
                completion_tokens
            )
            metrics.ai_total_tokens_total.labels(model_name=model).inc(
                prompt_tokens + completion_tokens
            )

            result = PrometheusResult.SUCCESS
            return data
    except HTTPException:
        raise
    except Exception as e:
        metrics.error_count_total.labels(error_type=type(e).__name__).inc()
        logger.error(f"Failed to proxy request to {LITELLM_COMPLETIONS_URL}: {e}")
        raise HTTPException(
            status_code=502,
            detail={"error": f"Failed to proxy request"},
        )
    finally:
        duration = time.time() - start_time
        metrics.chat_completion_latency.labels(result=result).observe(duration)
        metrics.ai_backend_request_duration_seconds.labels(model_name=model).observe(
            duration
        )
