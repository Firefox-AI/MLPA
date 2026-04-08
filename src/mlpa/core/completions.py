import json
import time

import httpx
from fastapi import HTTPException
from tenacity import retry, stop_after_attempt, wait_exponential

from mlpa.core.classes import AuthorizedChatRequest, LitellmRoutingSnapshot
from mlpa.core.config import (
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED,
    ERROR_CODE_MAX_USERS_REACHED,
    ERROR_CODE_RATE_LIMIT_EXCEEDED,
    ERROR_CODE_REQUEST_TOO_LARGE,
    ERROR_CODE_UPSTREAM_ERROR,
    LITELLM_COMPLETION_AUTH_HEADERS,
    LITELLM_COMPLETIONS_URL,
    env,
)
from mlpa.core.http_client import get_http_client
from mlpa.core.litellm_routing import parse_litellm_routing_headers
from mlpa.core.logger import logger
from mlpa.core.prometheus_metrics import (
    PrometheusRejectionReason,
    PrometheusResult,
    metrics,
)
from mlpa.core.utils import (
    get_or_create_user,
    is_context_window_error,
    is_litellm_upstream_rate_limit,
    is_rate_limit_error,
    litellm_request,
    log_litellm_retry_attempt,
    raise_and_log,
    should_retry_on_litellm_error,
)

_RATE_LIMIT_REJECTION: dict[int, tuple[PrometheusRejectionReason, str]] = {
    ERROR_CODE_BUDGET_LIMIT_EXCEEDED: (
        PrometheusRejectionReason.BUDGET_EXCEEDED,
        "86400",
    ),
    ERROR_CODE_RATE_LIMIT_EXCEEDED: (PrometheusRejectionReason.RATE_LIMITED, "60"),
    ERROR_CODE_UPSTREAM_ERROR: (PrometheusRejectionReason.UPSTREAM_ERROR, "60"),
}


@retry(
    wait=wait_exponential(multiplier=1, min=1, max=4),
    stop=stop_after_attempt(5),
    retry=lambda state: (
        should_retry_on_litellm_error(state.outcome.exception())
        if state.outcome.failed
        else False
    ),
    before_sleep=log_litellm_retry_attempt,
    reraise=True,
)
async def _call_litellm_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    headers: dict,
    json: dict,
    timeout: float,
    stream: bool = False,
):
    """Helper to make LiteLLM calls with retry logic."""
    return await litellm_request(
        client,
        method,
        url,
        headers,
        json=json,
        timeout=timeout,
        stream=stream,
    )


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
        elif is_litellm_upstream_rate_limit(error_text):
            return ERROR_CODE_UPSTREAM_ERROR
    except (json.JSONDecodeError, AttributeError, UnicodeDecodeError):
        pass

    return None


def _record_rejection(
    req: AuthorizedChatRequest, reason: PrometheusRejectionReason
) -> None:
    metrics.chat_request_rejections.labels(
        reason=reason,
        model=req.model,
        service_type=req.service_type,
        purpose=req.purpose,
    ).inc()


def record_chat_request_rejection(
    req: AuthorizedChatRequest, reason: PrometheusRejectionReason
) -> None:
    """Increment chat_request_rejections for failures outside completions (e.g. signup cap)."""
    _record_rejection(req, reason)


async def get_or_create_user_for_completion(user_id: str, req: AuthorizedChatRequest):
    """Wraps get_or_create_user and records a signup-cap rejection metric if applicable."""
    try:
        return await get_or_create_user(user_id)
    except HTTPException as exc:
        if (
            exc.status_code == 403
            and isinstance(exc.detail, dict)
            and exc.detail.get("error") == ERROR_CODE_MAX_USERS_REACHED
        ):
            _record_rejection(req, PrometheusRejectionReason.SIGNUP_CAP_EXCEEDED)
        raise


def _tool_names_from_request(tools: list) -> list[str]:
    """Extract function names from OpenAI-format tools list."""
    names = []
    for t in tools or []:
        if not isinstance(t, dict):
            continue
        fn = t.get("function", {})
        name = fn.get("name")
        names.append(name or "unknown")
    return names


def _record_litellm_routing_metrics(
    req: AuthorizedChatRequest,
    snapshot: LitellmRoutingSnapshot,
    prompt_tokens: int,
    completion_tokens: int,
) -> None:
    fallback_used = "true" if snapshot.attempted_fallbacks > 0 else "false"
    labels_base = {
        "requested_model": req.model,
        "backend": snapshot.backend,
        "service_type": req.service_type,
        "purpose": req.purpose,
    }
    metrics.litellm_routed_completions.labels(
        **labels_base,
        fallback_used=fallback_used,
    ).inc()
    metrics.litellm_attempted_fallbacks.labels(**labels_base).observe(
        snapshot.attempted_fallbacks
    )
    metrics.litellm_attempted_retries.labels(**labels_base).observe(
        snapshot.attempted_retries
    )
    if snapshot.response_duration_ms is not None:
        metrics.litellm_reported_duration_seconds.labels(
            **labels_base,
            fallback_used=fallback_used,
        ).observe(snapshot.response_duration_ms / 1000.0)
    if snapshot.response_cost_usd is not None:
        metrics.litellm_reported_cost_usd_total.labels(
            **labels_base,
            fallback_used=fallback_used,
        ).inc(snapshot.response_cost_usd)
    if prompt_tokens > 0:
        metrics.litellm_routed_tokens.labels(
            type="prompt",
            **labels_base,
            fallback_used=fallback_used,
        ).inc(prompt_tokens)
    if completion_tokens > 0:
        metrics.litellm_routed_tokens.labels(
            type="completion",
            **labels_base,
            fallback_used=fallback_used,
        ).inc(completion_tokens)


def _record_request_with_tools(req: AuthorizedChatRequest) -> None:
    if req.tools:
        for name in _tool_names_from_request(req.tools):
            metrics.chat_requests_with_tools.labels(
                tool_name=name,
                model=req.model,
                service_type=req.service_type,
                purpose=req.purpose,
            ).inc()


def _record_tool_metrics(
    model: str | None,
    service_type: str,
    purpose: str,
    tool_names: list[str],
) -> None:
    model_label = model or ""
    n_calls = len(tool_names)
    for name in tool_names:
        metrics.chat_tool_calls.labels(
            tool_name=name,
            model=model_label,
            service_type=service_type,
            purpose=purpose,
        ).inc()
        metrics.chat_completions_with_tools.labels(
            tool_name=name,
            model=model_label,
            service_type=service_type,
            purpose=purpose,
        ).inc()
        # Histogram: one observation per completion = total tool calls in that completion.
        metrics.chat_tool_calls_per_completion.labels(
            tool_name=name,
            model=model_label,
            service_type=service_type,
            purpose=purpose,
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
            exclude={"max_completion_tokens", "service_type", "purpose"},
            exclude_none=True,
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
        response = await _call_litellm_with_retry(
            client=client,
            method="POST",
            url=LITELLM_COMPLETIONS_URL,
            headers=LITELLM_COMPLETION_AUTH_HEADERS,
            json=body,
            timeout=env.STREAMING_TIMEOUT_SECONDS,
            stream=True,
        )
        try:
            litellm_routing_snapshot = parse_litellm_routing_headers(response.headers)

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
                    type="prompt",
                    model=authorized_chat_request.model,
                    service_type=authorized_chat_request.service_type,
                    purpose=authorized_chat_request.purpose,
                ).inc(prompt_tokens)
                metrics.chat_tokens_per_request.labels(
                    type="prompt",
                    model=authorized_chat_request.model,
                    service_type=authorized_chat_request.service_type,
                    purpose=authorized_chat_request.purpose,
                ).observe(prompt_tokens)
            if completion_tokens > 0:
                metrics.chat_tokens.labels(
                    type="completion",
                    model=authorized_chat_request.model,
                    service_type=authorized_chat_request.service_type,
                    purpose=authorized_chat_request.purpose,
                ).inc(completion_tokens)
                metrics.chat_tokens_per_request.labels(
                    type="completion",
                    model=authorized_chat_request.model,
                    service_type=authorized_chat_request.service_type,
                    purpose=authorized_chat_request.purpose,
                ).observe(completion_tokens)
            tool_names = [
                tool_calls_accum[i]["function"].get("name") or "unknown"
                for i in sorted(tool_calls_accum)
            ]
            _record_tool_metrics(
                authorized_chat_request.model,
                authorized_chat_request.service_type,
                authorized_chat_request.purpose,
                tool_names,
            )
            _record_litellm_routing_metrics(
                authorized_chat_request,
                litellm_routing_snapshot,
                prompt_tokens,
                completion_tokens,
            )
            result = PrometheusResult.SUCCESS
        finally:
            await response.aclose()
    except httpx.HTTPStatusError as e:
        if not streaming_started:
            # Read the error response content for streaming responses
            error_text_str = ""
            try:
                error_bytes = await e.response.aread()
                error_text_str = error_bytes.decode("utf-8") if error_bytes else ""
            except Exception:
                pass
            finally:
                await e.response.aclose()

            if e.response.status_code in {400, 429}:
                # Check for budget or rate limit errors
                error_code = _parse_rate_limit_error(
                    error_text_str, authorized_chat_request.user
                )
                if error_code in _RATE_LIMIT_REJECTION:
                    reason, _ = _RATE_LIMIT_REJECTION[error_code]
                    _record_rejection(authorized_chat_request, reason)
                    yield f'data: {{"error": {error_code}}}\n\n'.encode()
                    return

            # Context window exceeded: detect by error text or upstream 413
            if e.response.status_code == 413 or is_context_window_error(error_text_str):
                logger.warning(
                    f"Context window exceeded for user {authorized_chat_request.user}"
                )
                _record_rejection(
                    authorized_chat_request,
                    PrometheusRejectionReason.PAYLOAD_TOO_LARGE,
                )
                yield f'data: {{"error": {ERROR_CODE_REQUEST_TOO_LARGE}}}\n\n'.encode()
                return

            # For other errors or if we couldn't parse the error
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
            purpose=authorized_chat_request.purpose,
        ).observe(time.perf_counter() - start_time)


async def get_completion(authorized_chat_request: AuthorizedChatRequest):
    """
    Proxies a non-streaming request to LiteLLM.
    """
    start_time = time.perf_counter()
    _record_request_with_tools(authorized_chat_request)
    body = {
        **authorized_chat_request.model_dump(
            exclude={"max_completion_tokens", "service_type", "purpose"},
            exclude_none=True,
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
        response = await _call_litellm_with_retry(
            client=client,
            method="POST",
            url=LITELLM_COMPLETIONS_URL,
            headers=LITELLM_COMPLETION_AUTH_HEADERS,
            json=body,
            timeout=env.STREAMING_TIMEOUT_SECONDS,
        )
        litellm_routing_snapshot = parse_litellm_routing_headers(response.headers)
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
            type="prompt",
            model=authorized_chat_request.model,
            service_type=authorized_chat_request.service_type,
            purpose=authorized_chat_request.purpose,
        ).inc(prompt_tokens)
        metrics.chat_tokens_per_request.labels(
            type="prompt",
            model=authorized_chat_request.model,
            service_type=authorized_chat_request.service_type,
            purpose=authorized_chat_request.purpose,
        ).observe(prompt_tokens)
        metrics.chat_tokens.labels(
            type="completion",
            model=authorized_chat_request.model,
            service_type=authorized_chat_request.service_type,
            purpose=authorized_chat_request.purpose,
        ).inc(completion_tokens)
        metrics.chat_tokens_per_request.labels(
            type="completion",
            model=authorized_chat_request.model,
            service_type=authorized_chat_request.service_type,
            purpose=authorized_chat_request.purpose,
        ).observe(completion_tokens)
        tool_calls = (
            data.get("choices", [{}])[0].get("message", {}).get("tool_calls") or []
        )
        tool_names = [
            tc.get("function", {}).get("name") or "unknown" for tc in tool_calls
        ]
        _record_tool_metrics(
            authorized_chat_request.model,
            authorized_chat_request.service_type,
            authorized_chat_request.purpose,
            tool_names,
        )
        _record_litellm_routing_metrics(
            authorized_chat_request,
            litellm_routing_snapshot,
            prompt_tokens,
            completion_tokens,
        )
        result = PrometheusResult.SUCCESS
        return data
    except httpx.HTTPStatusError as e:
        error_text = e.response.text
        if e.response.status_code in {400, 429}:
            error_code = _parse_rate_limit_error(
                error_text, authorized_chat_request.user
            )
            if error_code in _RATE_LIMIT_REJECTION:
                reason, retry_after = _RATE_LIMIT_REJECTION[error_code]
                _record_rejection(authorized_chat_request, reason)
                raise HTTPException(
                    status_code=429,
                    detail={"error": error_code},
                    headers={"Retry-After": retry_after},
                )
        # Context window exceeded: detect by error text or upstream 413
        if e.response.status_code == 413 or is_context_window_error(error_text):
            logger.warning(
                f"Context window exceeded for user {authorized_chat_request.user}"
            )
            _record_rejection(
                authorized_chat_request, PrometheusRejectionReason.PAYLOAD_TOO_LARGE
            )
            raise HTTPException(
                status_code=413,
                detail={"error": ERROR_CODE_REQUEST_TOO_LARGE},
            )
        raise_and_log(e)
    except HTTPException:
        raise
    except Exception as e:
        raise_and_log(e, False, 502, "Failed to proxy request")
    finally:
        metrics.chat_completion_latency.labels(
            result=result,
            model=authorized_chat_request.model,
            service_type=authorized_chat_request.service_type,
            purpose=authorized_chat_request.purpose,
        ).observe(time.perf_counter() - start_time)
