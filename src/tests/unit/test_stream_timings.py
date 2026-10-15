from contextlib import asynccontextmanager

import httpx
import pytest

from mlpa.core.completions import stream_completion
from mlpa.core.request_timings import timing_snapshot
from tests.consts import SAMPLE_REQUEST
from tests.unit.test_request_timings import make_request


@pytest.mark.parametrize("mode", ["success", "connect_error", "read_error", "empty"])
async def test_stream_handshake_and_first_chunk_boundaries(mocker, metrics_spy, mode):
    now = [1.0]
    mocker.patch(
        "mlpa.core.request_timings.time.perf_counter", side_effect=lambda: now[0]
    )
    request = make_request("true")
    request.is_disconnected = mocker.AsyncMock(return_value=False)
    request.state.debug_timing_start = 1.0
    request.state.debug_spans = []
    closed = []

    async def chunks():
        now[0] = 1.2
        yield b""  # An empty chunk must not establish first-byte arrival.
        now[0] = 1.3
        if mode == "read_error":
            raise httpx.ReadError("broken stream")
        if mode == "empty":
            return
        yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
        now[0] = 1.5
        yield b"data: [DONE]\n\n"

    response = mocker.Mock(headers=httpx.Headers())
    response.aiter_bytes = chunks

    @asynccontextmanager
    async def stream(*args, **kwargs):
        now[0] = 1.1
        if mode == "connect_error":
            raise httpx.ConnectError("unreachable")
        try:
            yield response
        finally:
            closed.append(True)

    client = mocker.Mock(stream=stream)
    mocker.patch("mlpa.core.completions.get_http_client", return_value=client)
    mocker.patch(
        "mlpa.core.completions.redis_service.update_contracts",
        new_callable=mocker.AsyncMock,
    )
    received = []
    async for chunk in stream_completion(request, SAMPLE_REQUEST):
        received.append(chunk)
        if mode == "success" and len(received) == 1:
            # The first chunk is forwarded before reading the rest of the stream.
            assert now[0] == 1.3
            now[0] = 1.4

    spans = {span["name"]: span for span in timing_snapshot(request, now[0])}
    assert spans["upstream_handshake"] == {
        "name": "upstream_handshake",
        "start_ms": 0.0,
        "duration_ms": 100,
    }
    assert spans["request_preparation"]["duration_ms"] == 0
    if mode == "success":
        assert spans["upstream_first_chunk"] == {
            "name": "upstream_first_chunk",
            "start_ms": 0.0,
            "duration_ms": 300,
        }
        assert spans["upstream"]["duration_ms"] == 500
        assert received[-1] == b"data: [DONE]\n\n"
    else:
        assert "upstream_first_chunk" not in spans
        assert received and b"error" in received[-1]
    assert bool(closed) is (mode != "connect_error")
