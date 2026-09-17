"""Opt-in request timings, stored on the shared ASGI request state."""

import json
import time
from typing import TypedDict

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from mlpa.core.config import env
from mlpa.core.logger import logger


class TimingSpan(TypedDict):
    name: str
    start_ms: float
    duration_ms: float


def timing_snapshot(request: Request, end_time: float) -> list[TimingSpan]:
    """Copy completed spans using a common request-relative monotonic origin."""
    return [
        {
            "name": "total",
            "start_ms": 0.0,
            "duration_ms": round(
                (end_time - request.state.debug_timing_start) * 1_000, 2
            ),
        },
        *sorted(
            (span.copy() for span in request.state.debug_spans),
            key=lambda span: span["start_ms"],
        ),
    ]


class RequestDebugTiming:
    def __init__(self, timing_name: str, request: Request):
        self.name = timing_name
        self.request = request
        self.start_time: float | None = None

    def __enter__(self):
        self.start_time = None
        timing_key = self.request.headers.get("debug-timing-key")
        if timing_key and timing_key == env.MLPA_DEBUG_TIMING_KEY:
            self.start_time = time.perf_counter()
            if not hasattr(self.request.state, "debug_timing_start"):
                # Direct service calls outside middleware use the first span as origin.
                self.request.state.debug_timing_start = self.start_time
                self.request.state.debug_spans = []
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        if self.start_time is not None:
            span: TimingSpan = {
                "name": self.name,
                "start_ms": round(
                    (self.start_time - self.request.state.debug_timing_start) * 1_000, 2
                ),
                "duration_ms": round(
                    (time.perf_counter() - self.start_time) * 1_000, 2
                ),
            }
            self.request.state.debug_spans.append(span)


measure = RequestDebugTiming


class RequestTimingsMiddleware:
    """Add timings to debug JSON responses and log full response duration.

    The response total span ends at preparation; the logged total includes sending.
    Only unencoded JSON responses with a known length are buffered. Debug SSE
    responses get a final debug event after a terminal [DONE] event. Their
    original chunks are forwarded immediately; only a small suffix is retained.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        timing_key = request.headers.get("debug-timing-key")
        if not timing_key or timing_key != env.MLPA_DEBUG_TIMING_KEY:
            await self.app(scope, receive, send)
            return

        start_time = time.perf_counter()
        request.state.debug_timing_start = start_time
        request.state.debug_spans = []
        finished_at: float | None = None
        status_code = 500
        response_start: Message | None = None
        body = bytearray()
        is_event_stream = False
        stream_tail = b""

        async def send_timed(message: Message):
            nonlocal finished_at, status_code, response_start
            nonlocal is_event_stream, stream_tail
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = MutableHeaders(scope=message)
                content_type = headers.get("content-type", "").split(";", 1)[0].strip()
                is_event_stream = (
                    content_type == "text/event-stream"
                    and scope["method"] != "HEAD"
                    and status_code == 200
                    and "content-encoding" not in headers
                )
                if is_event_stream:
                    # Appending an event changes both length and validators.
                    for name in (
                        "content-length",
                        "etag",
                        "content-md5",
                        "digest",
                        "content-digest",
                    ):
                        if name in headers:
                            del headers[name]
                if (
                    scope["method"] != "HEAD"
                    and status_code not in {204, 206, 304}
                    and (
                        content_type == "application/json"
                        or content_type.endswith("+json")
                    )
                    and "content-length" in headers
                    and "content-encoding" not in headers
                ):
                    response_start = message
                    return
            elif message["type"] == "http.response.body" and response_start is not None:
                body.extend(message.get("body", b""))
                if message.get("more_body", False):
                    return
                response_body = bytes(body)
                try:
                    data = json.loads(response_body)
                except (ValueError, UnicodeDecodeError):
                    data = None
                if isinstance(data, dict):
                    data["spans"] = timing_snapshot(request, time.perf_counter())
                    response_body = json.dumps(data, ensure_ascii=True).encode("utf-8")
                    headers = MutableHeaders(scope=response_start)
                    headers["content-length"] = str(len(response_body))
                    # These validators describe the original representation.
                    for name in ("etag", "content-md5", "digest", "content-digest"):
                        if name in headers:
                            del headers[name]
                await send(response_start)
                response_start = None
                message = {**message, "body": response_body}
            if message["type"] == "http.response.body" and is_event_stream:
                chunk = message.get("body", b"")
                stream_tail = (stream_tail + chunk[-64:])[-64:]
                if not message.get("more_body", False):
                    normalized_tail = stream_tail.replace(b"\r\n", b"\n").replace(
                        b"\r", b"\n"
                    )
                    last_event = normalized_tail.rstrip(b"\n").split(b"\n\n")[-1]
                    if last_event in (b"data: [DONE]", b"data:[DONE]"):
                        timings = timing_snapshot(request, time.perf_counter())
                        separator = (
                            b"" if normalized_tail.endswith(b"\n\n") else b"\n\n"
                        )
                        event = (
                            b"data: "
                            + json.dumps({"spans": timings}).encode("utf-8")
                            + b"\n\n"
                        )
                        message = {**message, "body": chunk + separator + event}
            await send(message)
            if message["type"] == "http.response.body" and not message.get(
                "more_body", False
            ):
                finished_at = time.perf_counter()

        try:
            await self.app(scope, receive, send_timed)
        finally:
            end_time = finished_at if finished_at is not None else time.perf_counter()
            logger.bind(
                spans=timing_snapshot(request, end_time),
                method=scope["method"],
                path=scope["path"],
                status_code=status_code,
                response_complete=finished_at is not None,
            ).info("Request timings")
