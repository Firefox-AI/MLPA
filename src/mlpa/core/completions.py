import json
import time
from typing import Optional

import httpx
import tiktoken
from fastapi import HTTPException

from mlpa.core.classes import AuthorizedChatRequest
from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    LITELLM_COMPLETION_AUTH_HEADERS,
    LITELLM_COMPLETIONS_URL,
    env,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.logger import logger
from mlpa.core.prometheus_metrics import PrometheusResult, metrics
from mlpa.core.utils import is_rate_limit_error, raise_and_log

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


def _parse_rate_limit_error(error_text: str, user: str) -> int | None:
    """
    Parse error response to detect budget or rate limit errors.
    Returns the error code if a rate limit error is detected, None otherwise.
    """
    if not error_text:
        return None

    try:
        error_data = json.loads(error_text)
        if is_rate_limit_error(error_data, ["budget"]):
            logger.warning(f"Budget limit exceeded for user {user}: {error_text}")
            return ERROR_CODE_BUDGET_LIMIT_EXCEEDED
        elif is_rate_limit_error(error_data, ["rate"]):
            logger.warning(f"Rate limit exceeded for user {user}: {error_text}")
            return ERROR_CODE_RATE_LIMIT_EXCEEDED
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        pass

    return None


def _handle_rate_limit_error(error_text: str, user: str) -> None:
    error_code = _parse_rate_limit_error(error_text, user)
    if error_code == ERROR_CODE_BUDGET_LIMIT_EXCEEDED:
        raise HTTPException(
            status_code=429,
            detail={"error": ERROR_CODE_BUDGET_LIMIT_EXCEEDED},
            headers={"Retry-After": "86400"},
        )
    if error_code == ERROR_CODE_RATE_LIMIT_EXCEEDED:
        raise HTTPException(
            status_code=429,
            detail={"error": ERROR_CODE_RATE_LIMIT_EXCEEDED},
            headers={"Retry-After": "60"},
        )


def _tool_names_from_request(tools: list) -> list[str]:
    """Extract function names from OpenAI-format tools list."""
    names = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function")
        fn = t.get("function") or {}
        name = fn.get("name")
        names.append(name or "unknown")
    return names


def _record_request_with_tools(req: AuthorizedChatRequest) -> None:
    if req.tools:
        for name in _tool_names_from_request(req.tools):
            metrics.chat_requests_with_tools.labels(
                tool_name=name, model=req.model, service_type=req.service_type
            ).inc()


def _record_tool_metrics(model: str, service_type: str, tool_names: list[str]) -> None:
    n_calls = len(tool_names)
    for name in tool_names:
        metrics.chat_tool_calls.labels(
            tool_name=name, model=model, service_type=service_type
        ).inc()
        metrics.chat_completions_with_tools.labels(
            tool_name=name, model=model, service_type=service_type
        ).inc()
        # Histogram: one observation per completion = total tool calls in that completion.
        metrics.chat_tool_calls_per_completion.labels(
            tool_name=name, model=model, service_type=service_type
        ).observe(n_calls)


async def stream_completion(authorized_chat_request: AuthorizedChatRequest):
    """
    Proxies a streaming request to LiteLLM.
    Yields response chunks as they are received and logs metrics.
    """
    start_time = time.perf_counter()
    _record_request_with_tools(authorized_chat_request)
    body = {
        **authorized_chat_request.model_dump(
            exclude={"max_completion_tokens", "service_type"}, exclude_none=True
        ),
        "max_tokens": authorized_chat_request.max_completion_tokens,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    result = PrometheusResult.ERROR
    is_first_token = True
    prompt_tokens = 0
    completion_tokens = 0
    streaming_started = False
    tool_calls_accum: dict[int, dict] = {}
    logger.debug(
        f"Starting a stream completion using {authorized_chat_request.model}, for user {authorized_chat_request.user}",
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
                        error_text_str, authorized_chat_request.user
                    )
                    if error_code is not None:
                        yield f'data: {{"error": {error_code}}}\n\n'.encode()
                        return

                # For other errors or if we couldn't parse the error
                yield raise_and_log(e, True)
                return

            async for chunk in response.aiter_bytes():
                if is_first_token:
                    metrics.chat_completion_ttft.labels(
                        model=authorized_chat_request.model
                    ).observe(time.perf_counter() - start_time)
                    is_first_token = False
                    streaming_started = True

                try:
                    chunk_str = chunk.decode("utf-8")
                    for line in chunk_str.split("\n"):
                        if line.startswith("data: ") and line != "data: [DONE]":
                            data = json.loads(line[6:])
                            if "usage" in data:
                                usage = data["usage"]
                                prompt_tokens = usage.get("prompt_tokens", 0)
                                completion_tokens = usage.get("completion_tokens", 0)
                                if "prompt_tokens" not in usage:
                                    logger.warning(
                                        f"Missing 'prompt_tokens' in usage for model {authorized_chat_request.model}"
                                    )
                                if "completion_tokens" not in usage:
                                    logger.warning(
                                        f"Missing 'completion_tokens' in usage for model {authorized_chat_request.model}"
                                    )
                            for tc in (
                                data.get("choices", [{}])[0]
                                .get("delta", {})
                                .get("tool_calls", [])
                            ):
                                idx = tc.get("index", len(tool_calls_accum))
                                if idx not in tool_calls_accum:
                                    tool_calls_accum[idx] = {"function": {"name": ""}}
                                name = tc.get("function", {}).get("name")
                                if name:
                                    tool_calls_accum[idx]["function"]["name"] = (
                                        tool_calls_accum[idx]["function"]["name"]
                                        or name
                                    )
                except (json.JSONDecodeError, UnicodeDecodeError, KeyError):
                    pass

                yield chunk

            if prompt_tokens > 0:
                metrics.chat_tokens.labels(
                    type="prompt", model=authorized_chat_request.model
                ).inc(prompt_tokens)
            if completion_tokens > 0:
                metrics.chat_tokens.labels(
                    type="completion", model=authorized_chat_request.model
                ).inc(completion_tokens)
            tool_names = [
                tool_calls_accum[i]["function"].get("name") or "unknown"
                for i in sorted(tool_calls_accum)
            ]
            _record_tool_metrics(
                authorized_chat_request.model,
                authorized_chat_request.service_type,
                tool_names,
            )
            result = PrometheusResult.SUCCESS
    except httpx.HTTPStatusError as e:
        if not streaming_started:
            yield raise_and_log(e, True)
        else:
            logger.error(f"Upstream service returned an error: {e}")
    except Exception as e:
        if not streaming_started:
            yield raise_and_log(e, True, 502, "Failed to proxy request")
        else:
            logger.error(f"Upstream service returned an error: {e}")
    finally:
        metrics.chat_completion_latency.labels(
            result=result,
            model=authorized_chat_request.model,
            service_type=authorized_chat_request.service_type,
        ).observe(time.perf_counter() - start_time)


async def get_completion(authorized_chat_request: AuthorizedChatRequest):
    """
    Proxies a non-streaming request to LiteLLM.
    """
    start_time = time.perf_counter()
    _record_request_with_tools(authorized_chat_request)
    body = {
        **authorized_chat_request.model_dump(
            exclude={"max_completion_tokens", "service_type"}, exclude_none=True
        ),
        "max_tokens": authorized_chat_request.max_completion_tokens,
        "stream": False,
    }
    result = PrometheusResult.ERROR
    logger.debug(
        f"Starting a non-stream completion using {authorized_chat_request.model}, for user {authorized_chat_request.user}",
    )
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
                _handle_rate_limit_error(e.response.text, authorized_chat_request.user)
            raise_and_log(e)
        data = response.json()
        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        if "prompt_tokens" not in usage:
            logger.warning(
                f"Missing 'prompt_tokens' in usage for model {authorized_chat_request.model}"
            )
        if "completion_tokens" not in usage:
            logger.warning(
                f"Missing 'completion_tokens' in usage for model {authorized_chat_request.model}"
            )

        metrics.chat_tokens.labels(
            type="prompt", model=authorized_chat_request.model
        ).inc(prompt_tokens)
        metrics.chat_tokens.labels(
            type="completion", model=authorized_chat_request.model
        ).inc(completion_tokens)
        tool_calls = (
            data.get("choices", [{}])[0].get("message", {}).get("tool_calls") or []
        )
        tool_names = [
            tc.get("function", {}).get("name") or "unknown" for tc in tool_calls
        ]
        _record_tool_metrics(
            authorized_chat_request.model,
            authorized_chat_request.service_type,
            tool_names,
        )
        result = PrometheusResult.SUCCESS
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise_and_log(e, False, 502, "Failed to proxy request")
    finally:
        metrics.chat_completion_latency.labels(
            result=result,
            model=authorized_chat_request.model,
            service_type=authorized_chat_request.service_type,
        ).observe(time.perf_counter() - start_time)
