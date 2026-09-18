import asyncio

import pytest
from fastapi import Request

from mlpa.core.request_timings import RequestTimingsMiddleware, measure


def make_request(value=None):
    headers = [] if value is None else [(b"debug-timing", value.encode())]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


@pytest.mark.parametrize("value", [None, "", "false", "0", "1", "yes", "invalid"])
def test_disabled_timing_does_not_create_state(value):
    request = make_request(value)
    with measure("auth", request):
        pass
    assert not hasattr(request.state, "debug_spans")


def test_spans_preserve_each_occurrence_even_on_failure(mocker):
    request = make_request("true")
    mocker.patch(
        "mlpa.core.request_timings.time.perf_counter",
        side_effect=[1.0, 1.25, 2.0, 2.5, 3.0, 3.125],
    )
    with measure("auth", request):
        pass
    with measure("db", request):
        pass
    with pytest.raises(ValueError), measure("db", request):
        raise ValueError("failed query")
    assert request.state.debug_spans == [
        {"name": "auth", "start_ms": 0, "duration_ms": 250},
        {"name": "db", "start_ms": 1000, "duration_ms": 500},
        {"name": "db", "start_ms": 2000, "duration_ms": 125},
    ]


async def test_concurrent_requests_keep_separate_timings():
    async def run(stage):
        request = make_request("true")
        with measure(stage, request):
            await asyncio.sleep(0)
        assert {span["name"] for span in request.state.debug_spans} == {stage}
        return request.state.debug_spans

    first, second = await asyncio.gather(run("auth"), run("db"))
    assert [span["name"] for span in first] == ["auth"]
    assert [span["name"] for span in second] == ["db"]


@pytest.mark.parametrize("fail", [False, True])
async def test_middleware_records_full_stream_or_failure(mocker, fail):
    # A fake monotonic clock makes the stream/body/background boundaries exact.
    now = [1.0]
    mocker.patch(
        "mlpa.core.request_timings.time.perf_counter", side_effect=lambda: now[0]
    )
    log = mocker.patch("mlpa.core.request_timings.logger")
    request = make_request("true")

    async def app(scope, receive, send):
        with measure("upstream", Request(scope)):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send(
                {"type": "http.response.body", "body": b"first", "more_body": True}
            )
            now[0] = 2.0
            if fail:
                raise RuntimeError("stream disconnected")
        await send({"type": "http.response.body", "body": b"last"})
        now[0] = 3.0  # Background work must not extend total response duration.

    async def send(message):
        if message["type"] == "http.response.body" and not message.get("more_body"):
            now[0] = 2.5  # Include time spent sending the final chunk.

    middleware = RequestTimingsMiddleware(app)
    if fail:
        with pytest.raises(RuntimeError, match="stream disconnected"):
            await middleware(request.scope, mocker.AsyncMock(), send)
    else:
        await middleware(request.scope, mocker.AsyncMock(), send)

    fields = log.bind.call_args.kwargs
    assert fields["spans"] == [
        {"name": "total", "start_ms": 0.0, "duration_ms": 1000 if fail else 1500},
        {"name": "upstream", "start_ms": 0.0, "duration_ms": 1000},
    ]
    assert "debug_timings" not in fields
    assert fields["response_complete"] is not fail
    log.bind.return_value.info.assert_called_once_with("Request timings")


async def test_middleware_does_not_log_without_debug_header(mocker):
    log = mocker.patch("mlpa.core.request_timings.logger")
    request = make_request()
    app = mocker.AsyncMock()
    await RequestTimingsMiddleware(app)(
        request.scope, mocker.AsyncMock(), mocker.AsyncMock()
    )
    assert not hasattr(request.state, "debug_spans")
    app.assert_awaited_once()
    log.bind.assert_not_called()


@pytest.mark.parametrize(
    "value", [None, "", "false", "invalid", "true", "TRUE", " true "]
)
def test_middleware_adds_completion_timings(mocker, metrics_spy, value):
    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from mlpa.core.completions import get_completion
    from mlpa.core.middleware import register_middleware
    from tests.consts import SAMPLE_REQUEST, SUCCESSFUL_CHAT_RESPONSE

    app = FastAPI()
    register_middleware(app)

    @app.post("/completion")
    async def completion(request: Request):
        with measure("auth", request):
            pass
        with measure("db", request):
            pass
        return await get_completion(request, SAMPLE_REQUEST)

    response = httpx.Response(
        200,
        json=SUCCESSFUL_CHAT_RESPONSE,
        request=httpx.Request("POST", "http://upstream/"),
    )
    client = mocker.AsyncMock()
    client.post.return_value = response
    mocker.patch("mlpa.core.completions.get_http_client", return_value=client)
    with TestClient(app) as test_client:
        result = test_client.post(
            "/completion", headers={"debug-timing": value} if value else {}
        )
    assert result.status_code == 200
    assert int(result.headers["content-length"]) == len(result.content)
    data = result.json()
    assert "debug_timings" not in data
    if (value or "").strip().lower() == "true":
        spans = data["spans"]
        assert [span["name"] for span in spans] == ["total", "auth", "db", "upstream"]
        assert all(span["duration_ms"] >= 0 for span in spans)
        assert spans[0]["duration_ms"] >= sum(span["duration_ms"] for span in spans[1:])
    else:
        assert "spans" not in data


def test_streaming_timings_survive_registered_middleware(mocker):
    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from fastapi.testclient import TestClient

    from mlpa.core.middleware import register_middleware

    app = FastAPI()
    register_middleware(app)
    log = mocker.patch("mlpa.core.request_timings.logger")

    @app.get("/stream")
    async def stream(request: Request):
        with measure("auth", request):
            pass

        async def chunks():
            with measure("upstream", request):
                yield b"first"
                await asyncio.sleep(0)
                yield b"last"

        return StreamingResponse(chunks())

    with TestClient(app) as client:
        response = client.get("/stream", headers={"debug-timing": "true"})
    assert response.content == b"firstlast"
    fields = log.bind.call_args.kwargs
    spans = fields["spans"]
    assert [span["name"] for span in spans] == ["total", "auth", "upstream"]
    assert spans[0]["duration_ms"] >= spans[2]["duration_ms"]
    assert fields["response_complete"] is True


@pytest.mark.parametrize(
    "body,content_type,encoded,expected_debug",
    [
        (b'{"detail":"denied"}', "application/json", False, True),
        (b'{"detail":"denied"}', "application/problem+json", False, True),
        (b'["one","two"]', "application/json", False, False),
        (b"invalid json", "application/json", False, False),
        (b"plain text", "text/plain", False, False),
        (b"compressed", "application/json", True, False),
    ],
)
@pytest.mark.parametrize("status", [200, 201, 202, 400, 401, 403, 429, 500, 503])
async def test_json_response_handling(
    mocker, body, content_type, encoded, expected_debug, status
):
    import json

    request = make_request("true")
    messages = []

    async def app(scope, receive, send):
        headers = [
            (b"content-type", content_type.encode()),
            (b"content-length", str(len(body)).encode()),
            (b"x-custom", b"preserved"),
            (b"etag", b"original"),
        ]
        if encoded:
            headers.append((b"content-encoding", b"gzip"))
        await send(
            {"type": "http.response.start", "status": status, "headers": headers}
        )
        await send({"type": "http.response.body", "body": body[:3], "more_body": True})
        await send({"type": "http.response.body", "body": body[3:], "more_body": False})

    async def send(message):
        messages.append(message)

    await RequestTimingsMiddleware(app)(request.scope, mocker.AsyncMock(), send)
    assert messages[0]["status"] == status
    headers = dict(messages[0]["headers"])
    assert headers[b"x-custom"] == b"preserved"
    actual_body = b"".join(message.get("body", b"") for message in messages[1:])
    assert int(headers[b"content-length"]) == len(actual_body)
    if expected_debug and status == 200:
        data = json.loads(actual_body)
        assert data["detail"] == "denied"
        assert data["spans"][0]["duration_ms"] >= 0
        assert b"etag" not in headers
    else:
        assert actual_body == body
        assert headers[b"etag"] == b"original"


async def test_json_total_excludes_sending_time(mocker):
    import json

    now = [1.0]
    mocker.patch(
        "mlpa.core.request_timings.time.perf_counter", side_effect=lambda: now[0]
    )
    log = mocker.patch("mlpa.core.request_timings.logger")
    request = make_request("true")
    messages = []

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", b"2"),
                ],
            }
        )
        now[0] = 2.0
        await send({"type": "http.response.body", "body": b"{}"})

    async def send(message):
        messages.append(message)
        now[0] = 3.0

    await RequestTimingsMiddleware(app)(request.scope, mocker.AsyncMock(), send)
    assert json.loads(messages[-1]["body"])["spans"][0]["duration_ms"] == 1000
    assert log.bind.call_args.kwargs["spans"][0]["duration_ms"] == 2000


@pytest.mark.parametrize(
    "value", [None, "", "false", "invalid", "true", "TRUE", " true "]
)
@pytest.mark.parametrize(
    "ending",
    [
        b"data: [DONE]\n\n",
        b"data: [DONE]\r\n\r\n",
        b"data:[DONE]\n\n",
        b'data: {"error":"failed"}\n\n',
    ],
)
async def test_sse_debug_event_after_done_without_buffering(mocker, value, ending):
    import json

    request = make_request(value)
    messages = []
    original = b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n' + ending
    now = [1.0]
    mocker.patch(
        "mlpa.core.request_timings.time.perf_counter", side_effect=lambda: now[0]
    )

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream; charset=utf-8"),
                    (b"content-length", str(len(original)).encode()),
                ],
            }
        )
        with measure("upstream", Request(scope)):
            # Every-byte chunks exercise every possible split in the DONE marker.
            for value in original:
                chunk = bytes([value])
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
                assert messages[-1]["body"] == chunk  # Forwarded immediately.
            now[0] = 2.0
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def send(message):
        messages.append(message)

    await RequestTimingsMiddleware(app)(request.scope, mocker.AsyncMock(), send)
    result = b"".join(message.get("body", b"") for message in messages)
    enabled = (value or "").strip().lower() == "true"
    if enabled and b"[DONE]" in ending:
        assert result.startswith(original + b"data: ")
        debug = json.loads(result[len(original) + len(b"data: ") :])
        assert debug == {
            "spans": [
                {"name": "total", "start_ms": 0.0, "duration_ms": 1000},
                {"name": "upstream", "start_ms": 0.0, "duration_ms": 1000},
            ]
        }
        assert b"content-length" not in dict(messages[0]["headers"])
    else:
        assert result == original
    assert messages[-1]["more_body"] is False


def test_sse_debug_event_uses_completed_timings_through_middleware(mocker):
    import json

    from fastapi import FastAPI
    from fastapi.responses import StreamingResponse
    from fastapi.testclient import TestClient

    from mlpa.core.middleware import register_middleware

    app = FastAPI()
    register_middleware(app)

    @app.get("/stream")
    async def stream(request: Request):
        with measure("auth", request):
            pass

        async def chunks():
            with measure("upstream", request):
                yield b'data: {"choices":[]}\n\n'
                yield b"data: [DO"
                yield b"NE]\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    with TestClient(app) as client:
        response = client.get("/stream", headers={"debug-timing": "true"})
    events = response.content.split(b"\n\n")
    assert events[1] == b"data: [DONE]"
    payload = json.loads(events[2][len(b"data: ") :])
    assert set(payload) == {"spans"}
    spans = payload["spans"]
    assert [span["name"] for span in spans] == ["total", "auth", "upstream"]
    assert spans[0]["duration_ms"] >= spans[2]["duration_ms"]
    assert events[3] == b""


def test_nested_and_repeated_spans_preserve_offsets_precision_and_failures(mocker):
    from mlpa.core.request_timings import timing_snapshot

    request = make_request("true")
    request.state.debug_timing_start = 10.0
    request.state.debug_spans = []
    mocker.patch(
        "mlpa.core.request_timings.time.perf_counter",
        side_effect=[10.00125, 10.0025, 10.00375, 10.005, 10.00625, 10.0075],
    )
    with measure("auth", request):
        with measure("db", request):
            pass
    with pytest.raises(ValueError), measure("db", request):
        raise ValueError("failed")

    spans = timing_snapshot(request, 10.01)
    assert [span["name"] for span in spans] == ["total", "auth", "db", "db"]
    assert [span["start_ms"] for span in spans] == pytest.approx([0, 1.25, 2.5, 6.25])
    assert [span["duration_ms"] for span in spans] == pytest.approx(
        [10, 3.75, 1.25, 1.25]
    )
    # Exported spans are snapshots, not mutable references to request state.
    request.state.debug_spans[0]["duration_ms"] = 999
    assert spans[2]["duration_ms"] == pytest.approx(1.25)


def test_stage_and_total_spans_round_to_two_decimals(mocker):
    from mlpa.core.request_timings import timing_snapshot

    request = make_request("true")
    request.state.debug_timing_start = 10.0
    request.state.debug_spans = []
    mocker.patch(
        "mlpa.core.request_timings.time.perf_counter",
        side_effect=[10.001234, 10.009876],
    )
    with measure("auth", request):
        pass
    assert timing_snapshot(request, 10.012346) == [
        {"name": "total", "start_ms": 0.0, "duration_ms": 12.35},
        {"name": "auth", "start_ms": 1.23, "duration_ms": 8.64},
    ]


@pytest.mark.parametrize("status", [201, 400, 401, 403, 429, 500, 503])
async def test_non_200_sse_response_is_unchanged(mocker, status):
    request = make_request("true")
    body = b"data: [DONE]\n\n"
    original = [
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"text/event-stream"),
                (b"content-length", str(len(body)).encode()),
                (b"etag", b"original"),
            ],
        },
        {"type": "http.response.body", "body": body, "more_body": False},
    ]
    sent = []

    async def app(scope, receive, send):
        import copy

        for message in original:
            await send(copy.deepcopy(message))

    async def send(message):
        sent.append(message)

    await RequestTimingsMiddleware(app)(request.scope, mocker.AsyncMock(), send)
    assert sent == original
