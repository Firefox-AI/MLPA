"""Offline diagnostic for MLPA's disconnect-watcher cancellation race.

Run from MLPA: .venv/bin/python scripts/repro_stream_disconnect_cancellation.py

Loads the actual stream_completion function via AST without importing application
configuration/services. Uses real installed Starlette/AnyIO and StreamingResponse;
upstream HTTP, metrics, Redis and auth are replaced with in-memory stand-ins.
Deliberately schedules response cleanup during a disconnect poll, then compares
cancellation between polls. This diagnoses local code, not a deployed image.
"""

import ast
import asyncio
import contextlib
import hashlib
import importlib.metadata
import json
import socket
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from starlette.requests import Request
from starlette.responses import StreamingResponse

SOURCE = Path(__file__).resolve().parents[1] / "src/mlpa/core/completions.py"


async def trial(race):
    messages = asyncio.Queue()
    polling = asyncio.Event()
    cleanup_ready = False
    watcher = None
    received = []
    metrics = []
    events = []
    started = time.perf_counter()

    def mark(event):
        events.append(
            {"event": event, "ms": round((time.perf_counter() - started) * 1000, 3)}
        )

    async def receive():
        nonlocal watcher
        watcher = asyncio.current_task()
        if cleanup_ready:
            polling.set()
        return await messages.get()

    request = Request({"type": "http"}, receive=receive)

    class Upstream:
        headers = {}

        def raise_for_status(self):
            pass

        async def aiter_bytes(self):
            yield b'data: {"choices":[{"delta":{"content":"offline answer"}}]}\n\n'
            yield b"data: [DONE]\n\n"
            mark("upstream_eof")

    @contextlib.asynccontextmanager
    async def stream(*args, **kwargs):
        nonlocal cleanup_ready
        yield Upstream()
        cleanup_ready = True
        await polling.wait()
        if not race:
            # Let the disconnect check leave its internal cancellation scope.
            await asyncio.sleep(0.005)
        mark("upstream_context_closed")

    def latency(*args):
        metrics.append({"result": args[1], "seconds": args[2]})
        mark("latency_recorded")

    noop = lambda *args, **kwargs: None
    namespace = {
        "asyncio": asyncio,
        "contextlib": contextlib,
        "json": json,
        "time": time,
        "httpx": httpx,
        "AsyncIterator": AsyncIterator,
        "Request": Request,
        "AuthorizedChatRequest": object,
        "logger": MagicMock(),
        "env": SimpleNamespace(
            DISCONNECT_POLL_INTERVAL_SECONDS=0.1,
            STREAMING_TIMEOUT_SECONDS=300,
            HTTPX_CONNECT_TIMEOUT_SECONDS=30,
            HTTPX_WRITE_TIMEOUT_SECONDS=30,
            HTTPX_POOL_TIMEOUT_SECONDS=5,
        ),
        "PrometheusResult": SimpleNamespace(
            ERROR="error", SUCCESS="success", ABORT="abort"
        ),
        "AvailabilityReason": SimpleNamespace(
            UPSTREAM_ERROR="upstream_error",
            EMPTY_RESPONSE="empty",
            VALID_RESPONSE="valid",
            CLIENT_DISCONNECT="disconnect",
        ),
        "LITELLM_COMPLETIONS_URL": "http://offline.invalid/v1/chat/completions",
        "resolve_litellm_virtual_auth_headers": lambda *args: {},
        "_build_litellm_body": lambda *args, **kwargs: {},
        "record_request_with_tools": noop,
        "record_ttft": noop,
        "record_completion_success": noop,
        "record_chat_availability": noop,
        "record_completion_latency": latency,
        "get_http_client": lambda: SimpleNamespace(stream=stream),
        "measure": lambda *args: contextlib.nullcontext(),
        "parse_litellm_routing_headers": lambda *args: None,
        "extract_tool_names": lambda *args: [],
        "redis_service": SimpleNamespace(update_contracts=AsyncMock()),
    }
    tree = ast.parse(SOURCE.read_text(), filename=str(SOURCE))
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "stream_completion"
    )
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), str(SOURCE), "exec"),
        namespace,
    )
    auth = SimpleNamespace(
        log_fields={},
        model="offline",
        user="offline",
        litellm_virtual_key=None,
        service_type="answer",
    )
    generator = namespace["stream_completion"](request, auth)

    async def send(message):
        received.append(message)
        if b"[DONE]" in message.get("body", b""):
            mark("client_received_done")
        if message["type"] == "http.response.body" and not message.get(
            "more_body", False
        ):
            mark("client_received_http_eof")

    response = StreamingResponse(generator, media_type="text/event-stream")
    task = asyncio.create_task(
        response({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)
    )
    done, _ = await asyncio.wait({task}, timeout=0.25)
    frame = generator.ag_frame
    result = {
        "case": "cancel_during_poll" if race else "cancel_between_polls",
        "stalled": not bool(done),
        "received_done": any(b"[DONE]" in m.get("body", b"") for m in received),
        "received_http_eof_before_disconnect": any(
            m["type"] == "http.response.body" and not m.get("more_body", False)
            for m in received
        ),
        "latency_recorded_before_disconnect": bool(metrics),
        "watcher_cancelling": watcher.cancelling() if watcher else None,
        "blocked_location": f"{frame.f_code.co_filename}:{frame.f_lineno}"
        if frame
        else None,
    }
    # Simulate the eventual disconnect, without cancelling the parent again.
    if not done:
        mark("inject_client_disconnect")
        messages.put_nowait({"type": "http.disconnect"})
        done, _ = await asyncio.wait({task}, timeout=0.2)
        if not done:
            task.cancel()
            raise RuntimeError("Diagnostic cleanup failed")
    await task
    result.update(events=events, metrics=metrics)
    return result


async def main():
    results = [await trial(race) for race in (False, True) for _ in range(10)]
    print(
        json.dumps(
            {
                "source": str(SOURCE),
                "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
                "python": sys.version,
                "versions": {
                    name: importlib.metadata.version(name)
                    for name in ("starlette", "anyio", "httpx")
                },
                "summary": {
                    name: {
                        "trials": sum(r["case"] == name for r in results),
                        "stalls": sum(
                            r["case"] == name and r["stalled"] for r in results
                        ),
                    }
                    for name in ("cancel_between_polls", "cancel_during_poll")
                },
                "examples": [results[0], results[10]],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    with (
        patch.object(
            socket.socket, "connect", side_effect=AssertionError("Network disabled")
        ),
        patch.object(
            socket.socket, "connect_ex", side_effect=AssertionError("Network disabled")
        ),
    ):
        asyncio.run(main())
