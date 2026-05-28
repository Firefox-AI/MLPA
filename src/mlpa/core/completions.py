import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator

import httpx
from fastapi import HTTPException, Request

from mlpa.core.classes import AuthorizedChatRequest, AuthorizedSearchRequest
from mlpa.core.config import (
    ERROR_CODE_MAX_USERS_REACHED,
    LITELLM_COMPLETIONS_URL,
    LITELLM_VIRTUAL_AUTH_HEADERS,
    env,
)
from mlpa.core.errors import classify_upstream_error
from mlpa.core.http_client import get_http_client
from mlpa.core.litellm_routing import parse_litellm_routing_headers
from mlpa.core.logger import logger
from mlpa.core.metrics import (
    extract_tool_names,
    record_chat_request_rejection,
    record_completion_latency,
    record_completion_success,
    record_request_with_tools,
    record_ttft,
)
from mlpa.core.prometheus_metrics import (
    PrometheusRejectionReason,
    PrometheusResult,
)
from mlpa.core.utils import (
    get_or_create_user,
    raise_and_log,
)


def _build_litellm_body(req: AuthorizedChatRequest, *, stream: bool) -> dict:
    body = req.model_dump(
        exclude={"max_completion_tokens", "service_type", "purpose"},
        exclude_none=True,
    )
    body["max_tokens"] = req.max_completion_tokens
    body["stream"] = stream
    if stream:
        body["stream_options"] = {"include_usage": True}
    return body


async def get_or_create_user_for_completion(
    user_id: str, req: AuthorizedChatRequest | AuthorizedSearchRequest
):
    """Wraps get_or_create_user and records a signup-cap rejection metric if applicable."""
    try:
        return await get_or_create_user(user_id)
    except HTTPException as exc:
        if (
            exc.status_code == 403
            and isinstance(exc.detail, dict)
            and exc.detail.get("error") == ERROR_CODE_MAX_USERS_REACHED
            and isinstance(req, AuthorizedChatRequest)
        ):
            record_chat_request_rejection(
                req,
                PrometheusRejectionReason.SIGNUP_CAP_EXCEEDED,
            )
        raise


async def stream_completion(
    authorized_chat_request: AuthorizedChatRequest, request: Request
):
    """
    Proxies a streaming request to LiteLLM.
    Yields response chunks as they are received and logs metrics.
    """
    start_time = time.perf_counter()
    record_request_with_tools(authorized_chat_request)
    body = _build_litellm_body(authorized_chat_request, stream=True)
    result = PrometheusResult.ERROR
    is_first_token = True
    prompt_tokens = 0
    completion_tokens = 0
    streaming_started = False
    tool_calls_accum: dict[int, dict] = {}
    logger.debug(
        f"Starting a stream completion using {authorized_chat_request.model}, for user {authorized_chat_request.user}",
    )

    disconnect_event = asyncio.Event()
    _client_disconnected_msg = (
        f"Client disconnected mid-stream for user {authorized_chat_request.user}"
    )

    async def _watch_disconnect() -> None:
        while not await request.is_disconnected():
            await asyncio.sleep(env.DISCONNECT_POLL_INTERVAL_SECONDS)
        disconnect_event.set()

    async def _read_next_chunk(
        response_iterator: AsyncIterator[bytes],
    ) -> bytes:
        return await response_iterator.__anext__()

    watch_task = asyncio.create_task(_watch_disconnect())
    next_chunk_task: asyncio.Task[bytes] | None = None
    try:
        client = get_http_client()
        async with client.stream(
            "POST",
            LITELLM_COMPLETIONS_URL,
            headers=LITELLM_VIRTUAL_AUTH_HEADERS,
            json=body,
            timeout=httpx.Timeout(
                read=env.STREAMING_TIMEOUT_SECONDS,
                connect=env.HTTPX_CONNECT_TIMEOUT_SECONDS,
                write=env.HTTPX_WRITE_TIMEOUT_SECONDS,
                pool=env.HTTPX_POOL_TIMEOUT_SECONDS,
            ),
        ) as response:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                error_text_str = ""
                try:
                    error_bytes = await e.response.aread()
                    error_text_str = error_bytes.decode("utf-8") if error_bytes else ""
                except Exception:
                    pass

                match = classify_upstream_error(
                    error_text=error_text_str,
                    status_code=e.response.status_code,
                    user=authorized_chat_request.user,
                )
                if match is not None:
                    if match.log_message:
                        logger.warning(match.log_message)
                    record_chat_request_rejection(authorized_chat_request, match.reason)
                    yield f'data: {{"error": {match.error_code}}}\n\n'.encode()
                    return

                yield raise_and_log(e, True)
                return

            litellm_routing_snapshot = parse_litellm_routing_headers(response.headers)
            response_iterator = response.aiter_bytes()

            while True:
                if next_chunk_task is None:
                    next_chunk_task = asyncio.create_task(
                        _read_next_chunk(response_iterator)
                    )

                done, _ = await asyncio.wait(
                    {next_chunk_task, watch_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if watch_task in done:
                    watch_task.result()
                    result = PrometheusResult.ABORT
                    logger.info(_client_disconnected_msg)
                    if not next_chunk_task.done():
                        next_chunk_task.cancel()
                    with contextlib.suppress(
                        asyncio.CancelledError,
                        StopAsyncIteration,
                        httpx.ReadError,
                    ):
                        await next_chunk_task
                    with contextlib.suppress(httpx.ReadError):
                        await response.aclose()
                    break

                try:
                    chunk = next_chunk_task.result()
                except StopAsyncIteration:
                    break
                except httpx.ReadError:
                    if disconnect_event.is_set() or await request.is_disconnected():
                        disconnect_event.set()
                        result = PrometheusResult.ABORT
                        logger.info(_client_disconnected_msg)
                        break
                    raise
                finally:
                    next_chunk_task = None

                if is_first_token:
                    record_ttft(
                        authorized_chat_request.model,
                        time.perf_counter() - start_time,
                    )
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

            if result == PrometheusResult.ABORT:
                return

            if not streaming_started:
                yield raise_and_log(
                    RuntimeError("LiteLLM returned an empty response"),
                    True,
                    502,
                    "Empty response from upstream",
                )
                return

            tool_names = extract_tool_names(
                tool_calls_accum[i] for i in sorted(tool_calls_accum)
            )
            record_completion_success(
                authorized_chat_request,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                tool_names=tool_names,
                snapshot=litellm_routing_snapshot,
            )
            result = PrometheusResult.SUCCESS
    except httpx.ReadError as e:
        if disconnect_event.is_set() or await request.is_disconnected():
            disconnect_event.set()
            result = PrometheusResult.ABORT
            logger.info(_client_disconnected_msg)
        else:
            yield raise_and_log(e, True, 502, "Failed to proxy request")
    except Exception as e:
        yield raise_and_log(e, True, 502, "Failed to proxy request")
    finally:
        if next_chunk_task is not None:
            if not next_chunk_task.done():
                next_chunk_task.cancel()
            with contextlib.suppress(
                asyncio.CancelledError,
                StopAsyncIteration,
                httpx.ReadError,
            ):
                await next_chunk_task
        # Cancel the disconnect watcher and wait for it to finish to avoid
        # "Task was destroyed but it is pending" warnings at shutdown.
        watch_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch_task
        if result == PrometheusResult.ERROR and disconnect_event.is_set():
            result = PrometheusResult.ABORT
            logger.info(_client_disconnected_msg)
        record_completion_latency(
            authorized_chat_request, result, time.perf_counter() - start_time
        )


async def get_completion(authorized_chat_request: AuthorizedChatRequest):
    """
    Proxies a non-streaming request to LiteLLM.
    """
    start_time = time.perf_counter()
    record_request_with_tools(authorized_chat_request)
    body = _build_litellm_body(authorized_chat_request, stream=False)
    result = PrometheusResult.ERROR
    logger.debug(
        f"Starting a non-stream completion using {authorized_chat_request.model}, for user {authorized_chat_request.user}",
    )
    try:
        client = get_http_client()
        response = await client.post(
            LITELLM_COMPLETIONS_URL,
            headers=LITELLM_VIRTUAL_AUTH_HEADERS,
            json=body,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            match = classify_upstream_error(
                error_text=e.response.text,
                status_code=e.response.status_code,
                user=authorized_chat_request.user,
            )
            if match is not None:
                if match.log_message:
                    logger.warning(match.log_message)
                record_chat_request_rejection(authorized_chat_request, match.reason)
                headers = (
                    {"Retry-After": match.retry_after} if match.retry_after else None
                )
                raise HTTPException(
                    status_code=match.http_status,
                    detail={"error": match.error_code},
                    headers=headers,
                )
            raise_and_log(e)
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

        tool_calls = (
            data.get("choices", [{}])[0].get("message", {}).get("tool_calls") or []
        )
        tool_names = extract_tool_names(tool_calls)
        record_completion_success(
            authorized_chat_request,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            tool_names=tool_names,
            snapshot=litellm_routing_snapshot,
        )
        result = PrometheusResult.SUCCESS
        return data
    except HTTPException:
        raise
    except Exception as e:
        raise_and_log(e, False, 502, "Failed to proxy request")
    finally:
        record_completion_latency(
            authorized_chat_request, result, time.perf_counter() - start_time
        )
