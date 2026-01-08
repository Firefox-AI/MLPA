import json
import time
from typing import Optional

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
    env,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.prometheus_metrics import PrometheusResult, metrics
from mlpa.core.utils import is_rate_limit_error

# Global default tokenizer - initialized once at module load time
_global_default_tokenizer: Optional[tiktoken.Encoding] = None


def get_default_tokenizer() -> tiktoken.Encoding:
    """
    Get or create the global default tokenizer.
    """
    global _global_default_tokenizer
    if _global_default_tokenizer is None:
        try:
            _global_default_tokenizer = tiktoken.encoding_for_model(env.MODEL_NAME)
        except KeyError:
            _global_default_tokenizer = tiktoken.get_encoding("cl100k_base")
    return _global_default_tokenizer


def _parse_rate_limit_error(error_text: str, user: str, model_name: str) -> int | None:
    """
    Parse error response to detect budget or rate limit errors.
    Returns the error code if a rate limit error is detected, None otherwise.
    """
    if not error_text:
        return None

    try:
        error_data = json.loads(error_text)
        if is_rate_limit_error(error_data, ["budget"]):
            metrics.ai_error_count_total.labels(
                model_name=model_name, error="BudgetExceeded"
            ).inc()
            logger.warning(
                "Budget limit exceeded",
                extra={
                    "user_id": user,
                    "model_name": model_name,
                    "error": "BudgetExceeded",
                },
            )
            return ERROR_CODE_BUDGET_LIMIT_EXCEEDED
        elif is_rate_limit_error(error_data, ["rate"]):
            metrics.ai_error_count_total.labels(
                model_name=model_name, error="RateLimitExceeded"
            ).inc()
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "user_id": user,
                    "model_name": model_name,
                    "error": "RateLimitExceeded",
                },
            )
            return ERROR_CODE_RATE_LIMIT_EXCEEDED
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        metrics.ai_error_count_total.labels(
            model_name=model_name, error="UserThrottled"
        ).inc()
        logger.warning(
            "User throttled",
            extra={"user_id": user, "model_name": model_name, "error": "UserThrottled"},
        )

    return None


def _handle_rate_limit_error(
    e: httpx.HTTPStatusError, user: str, model_name: str
) -> None:
    error_text = e.response.text
    error_code = _parse_rate_limit_error(error_text, user, model_name)
    if error_code == ERROR_CODE_BUDGET_LIMIT_EXCEEDED:
        raise HTTPException(
            status_code=429,
            detail={"error": ERROR_CODE_BUDGET_LIMIT_EXCEEDED},
            headers={"Retry-After": "86400"},
        )
    elif error_code == ERROR_CODE_RATE_LIMIT_EXCEEDED:
        raise HTTPException(
            status_code=429,
            detail={"error": ERROR_CODE_RATE_LIMIT_EXCEEDED},
            headers={"Retry-After": "60"},
        )


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
            "user_id": authorized_chat_request.user,
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
        extra={
            "user_id": authorized_chat_request.user,
            "model": authorized_chat_request.model,
        },
    )
    try:
        client = get_http_client()
        async with client.stream(
            "POST",
            LITELLM_COMPLETIONS_URL,
            headers=LITELLM_COMPLETION_AUTH_HEADERS,
            json=body,
            timeout=env.STREAMING_TIMEOUT_SECONDS,
        ) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                # Read the error response content for streaming responses
                error_text_str = ""
                try:
                    error_bytes = await e.response.aread()
                    error_text_str = error_bytes.decode("utf-8") if error_bytes else ""
                except Exception:
                    pass

                if e.response.status_code in {400, 429}:
                    # Check for budget or rate limit errors
                    error_code = _parse_rate_limit_error(
                        error_text_str, authorized_chat_request.user, model
                    )
                    if error_code is not None:
                        yield f'data: {{"error": {error_code}}}\n\n'.encode()
                        return

                # For other errors or if we couldn't parse the error
                logger.error(
                    f"Upstream service returned an error: {e.response.status_code} - {error_text_str}"
                )
                yield f'data: {{"error": "Upstream service returned an error"}}\n\n'.encode()
                return

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
                            "user_id": authorized_chat_request.user,
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

            tokenizer = get_default_tokenizer()
            prompt_text = "".join(
                message["content"] for message in authorized_chat_request.messages
            )
            prompt_tokens = len(tokenizer.encode(prompt_text))
            metrics.ai_token_count_total.labels(model_name=model, type="prompt").inc(
                prompt_tokens
            )
            metrics.ai_token_count_total.labels(
                model_name=model, type="completion"
            ).inc(num_completion_tokens)
            logger.info(
                "Token generation summary",
                extra={
                    "user_id": authorized_chat_request.user,
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
                "user_id": authorized_chat_request.user,
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
                "user_id": authorized_chat_request.user,
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
                "user_id": authorized_chat_request.user,
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
            "user_id": authorized_chat_request.user,
            "model": model,
            "stream": False,
            "temperature": authorized_chat_request.temperature,
            "top_p": authorized_chat_request.top_p,
            "max_tokens": authorized_chat_request.max_completion_tokens,
        },
    )

    metrics.ai_request_count_total.labels(model_name=model).inc()

    try:
        client = get_http_client()
        response = await client.post(
            LITELLM_COMPLETIONS_URL,
            headers=LITELLM_COMPLETION_AUTH_HEADERS,
            json=body,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            if e.response.status_code in {400, 429}:
                _handle_rate_limit_error(e, authorized_chat_request.user, model)
            metrics.ai_error_count_total.labels(
                model_name=model, error=f"HTTP_{e.response.status_code}"
            ).inc()
            logger.error(
                "Upstream HTTP error",
                extra={
                    "user_id": authorized_chat_request.user,
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
                detail={"error": "Upstream service returned an error"},
            )
        data = response.json()
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        metrics.ai_token_count_total.labels(model_name=model, type="prompt").inc(
            prompt_tokens
        )
        metrics.ai_token_count_total.labels(model_name=model, type="completion").inc(
            completion_tokens
        )
        logger.info(
            "Token generation summary",
            extra={
                "user_id": authorized_chat_request.user,
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
                "user_id": authorized_chat_request.user,
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
                "user_id": authorized_chat_request.user,
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
                "user_id": authorized_chat_request.user,
                "model": model,
                "stream": True,
                "temperature": authorized_chat_request.temperature,
                "top_p": authorized_chat_request.top_p,
                "max_tokens": authorized_chat_request.max_completion_tokens,
                "duration": duration,
            },
        )
