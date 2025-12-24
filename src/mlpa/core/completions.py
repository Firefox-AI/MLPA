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
    try:
        error_text = e.response.text
        if error_text:
            error_data = json.loads(error_text)
            if is_rate_limit_error(error_data, ["budget"]):
                metrics.auth_error_count_total.labels(error="BudgetExceeded").inc()
                logger.warning(
                    "Budget limit exceeded",
                    extra={"error_type": "BudgetExceeded"},
                )
                raise HTTPException(
                    status_code=429,
                    detail={"error": ERROR_CODE_BUDGET_LIMIT_EXCEEDED},
                    headers={"Retry-After": "86400"},
                )
            elif is_rate_limit_error(error_data, ["rate"]):
                metrics.auth_error_count_total.labels(error="RateLimitExceeded").inc()
                logger.warning(
                    "Rate limit exceeded",
                    extra={"error_type": "RateLimitExceeded"},
                )
                raise HTTPException(
                    status_code=429,
                    detail={"error": ERROR_CODE_RATE_LIMIT_EXCEEDED},
                    headers={"Retry-After": "60"},
                )
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        metrics.auth_error_count_total.labels(error="UserThrottled").inc()
        logger.warning(
            "User throttled",
            extra={"error_type": "UserThrottled"},
        )
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

    metrics.ai_request_count_total.labels(model_name=model).inc()
    logger.info(
        "Completion request initiated",
        extra={
            "model": model,
            "stream": True,
            "temperature": authorized_chat_request.temperature,
            "top_p": authorized_chat_request.top_p,
            "max_tokens": authorized_chat_request.max_completion_tokens,
        },
    )

    result = PrometheusResult.ERROR
    is_first_token = True
    num_completion_tokens = 0
    streaming_started = False
    logger.debug(
        "Stream completion loop started",
        extra={"model": authorized_chat_request.model},
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
                        metrics.ai_error_count_total.labels(
                            model_name=model, error="BackendTimeout"
                        ).inc()
                        logger.error(
                            "Upstream backend timeout",
                            extra={
                                "model": model,
                                "stream": True,
                                "temperature": authorized_chat_request.temperature,
                                "top_p": authorized_chat_request.top_p,
                                "max_tokens": authorized_chat_request.max_completion_tokens,
                            },
                        )
                    raise e
                async for chunk in response.aiter_bytes():
                    num_completion_tokens += 1
                    if is_first_token:
                        duration = time.time() - start_time
                        metrics.ai_time_to_first_token.labels(
                            model_name=authorized_chat_request.model
                        ).observe(duration)
                        logger.info(
                            "First token generated",
                            extra={
                                "model": model,
                                "stream": True,
                                "temperature": authorized_chat_request.temperature,
                                "top_p": authorized_chat_request.top_p,
                                "max_tokens": authorized_chat_request.max_completion_tokens,
                                "duration": duration,
                            },
                        )
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
                metrics.ai_token_count_total.labels(
                    model_name=model, type="prompt"
                ).inc(prompt_tokens)
                metrics.ai_token_count_total.labels(
                    model_name=model, type="completion"
                ).inc(num_completion_tokens)
                logger.info(
                    "Token generation summary",
                    extra={
                        "model": model,
                        "stream": True,
                        "temperature": authorized_chat_request.temperature,
                        "top_p": authorized_chat_request.top_p,
                        "max_tokens": authorized_chat_request.max_completion_tokens,
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": num_completion_tokens,
                    },
                )

                result = PrometheusResult.SUCCESS
    except httpx.HTTPStatusError as e:
        metrics.ai_error_count_total.labels(
            model_name=model, error=f"HTTP_{e.response.status_code}"
        ).inc()
        logger.error(
            "Upstream HTTP error",
            extra={
                "model": model,
                "stream": True,
                "temperature": authorized_chat_request.temperature,
                "top_p": authorized_chat_request.top_p,
                "max_tokens": authorized_chat_request.max_completion_tokens,
                "status_code": e.response.status_code,
            },
        )
        if not streaming_started:
            yield f'data: {{"error": "Upstream service returned an error"}}\n\n'.encode()
    except Exception as e:
        metrics.ai_error_count_total.labels(error_type=type(e).__name__).inc()
        logger.error(
            "Stream completion proxy failed",
            extra={
                "model": model,
                "stream": True,
                "temperature": authorized_chat_request.temperature,
                "top_p": authorized_chat_request.top_p,
                "max_tokens": authorized_chat_request.max_completion_tokens,
                "error_type": type(e).__name__,
            },
        )
        if not streaming_started:
            yield f'data: {{"error": "Failed to proxy request"}}\n\n'.encode()
    finally:
        duration = time.time() - start_time
        metrics.ai_request_duration_seconds.labels(
            model_name=model, streaming=True
        ).observe(duration)
        logger.info(
            "Stream request finished",
            extra={
                "model": model,
                "stream": True,
                "temperature": authorized_chat_request.temperature,
                "top_p": authorized_chat_request.top_p,
                "max_tokens": authorized_chat_request.max_completion_tokens,
                "duration": duration,
            },
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
        "Non-stream completion initiated",
        extra={
            "model": model,
            "stream": False,
            "temperature": authorized_chat_request.temperature,
            "top_p": authorized_chat_request.top_p,
            "max_tokens": authorized_chat_request.max_completion_tokens,
        },
    )

    metrics.ai_request_count_total.labels(model_name=model).inc()

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
                    logger.warning(
                        "Upstream rate limit or bad request",
                        extra={
                            "model": model,
                            "stream": False,
                            "temperature": authorized_chat_request.temperature,
                            "top_p": authorized_chat_request.top_p,
                            "max_tokens": authorized_chat_request.max_completion_tokens,
                            "status_code": e.response.status_code,
                        },
                    )
                    await _handle_rate_limit_error(e, authorized_chat_request.user)
                elif e.response.status_code == 408:
                    metrics.ai_error_count_total.labels(
                        model_name=model, error="Timeout"
                    ).inc()
                    logger.error(
                        "Upstream backend timeout",
                        extra={
                            "model": model,
                            "stream": False,
                            "temperature": authorized_chat_request.temperature,
                            "top_p": authorized_chat_request.top_p,
                            "max_tokens": authorized_chat_request.max_completion_tokens,
                        },
                    )
                else:
                    metrics.ai_error_count_total.labels(
                        model_name=model, error=f"HTTP_{e.response.status_code}"
                    ).inc()
                    logger.error(
                        "Upstream HTTP error",
                        extra={
                            "model": model,
                            "stream": False,
                            "temperature": authorized_chat_request.temperature,
                            "top_p": authorized_chat_request.top_p,
                            "max_tokens": authorized_chat_request.max_completion_tokens,
                            "status_code": e.response.status_code,
                        },
                    )
                raise HTTPException(
                    status_code=e.response.status_code,
                    detail=f"Upstream service returned an error",
                )
            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)

            metrics.ai_token_count_total.labels(model_name=model, type="prompt").inc(
                prompt_tokens
            )
            metrics.ai_token_count_total.labels(
                model_name=model, type="completion"
            ).inc(completion_tokens)
            logger.info(
                "Token generation summary",
                extra={
                    "model": model,
                    "stream": False,
                    "temperature": authorized_chat_request.temperature,
                    "top_p": authorized_chat_request.top_p,
                    "max_tokens": authorized_chat_request.max_completion_tokens,
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                },
            )

            result = PrometheusResult.SUCCESS
            return data
    except HTTPException as e:
        logger.error(
            "Upstream service HTTP exception",
            extra={
                "model": model,
                "stream": False,
                "temperature": authorized_chat_request.temperature,
                "top_p": authorized_chat_request.top_p,
                "max_tokens": authorized_chat_request.max_completion_tokens,
                "status_code": e.status_code,
            },
        )
        raise
    except Exception as e:
        metrics.ai_error_count_total.labels(
            model_name=model, error=type(e).__name__
        ).inc()
        logger.error(
            "Proxy request failed",
            extra={
                "model": model,
                "stream": False,
                "temperature": authorized_chat_request.temperature,
                "top_p": authorized_chat_request.top_p,
                "max_tokens": authorized_chat_request.max_completion_tokens,
                "error_message": str(e),
                "error_type": type(e).__name__,
            },
        )
        raise HTTPException(
            status_code=502,
            detail={"error": f"Failed to proxy request"},
        )
    finally:
        duration = time.time() - start_time
        metrics.ai_request_duration_seconds.labels(
            model_name=model, streaming=False
        ).observe(duration)
        logger.info(
            "Request finished",
            extra={
                "model": model,
                "stream": True,
                "temperature": authorized_chat_request.temperature,
                "top_p": authorized_chat_request.top_p,
                "max_tokens": authorized_chat_request.max_completion_tokens,
                "duration": duration,
            },
        )
