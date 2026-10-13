import json

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import field_validator

from mlpa.core.classes import ChatRequest
from mlpa.core.middleware import register_middleware
from mlpa.core.request_preprocessing import TimingRequest
from mlpa.core.request_timings import measure


async def test_upload_and_parsing_are_separate_and_cached(mocker):
    now = [1.0]
    mocker.patch(
        "mlpa.core.request_timings.time.perf_counter", side_effect=lambda: now[0]
    )
    original_loads = json.loads

    def loads(body):
        now[0] += 0.2
        return original_loads(body)

    mocker.patch("starlette.requests.json.loads", side_effect=loads)

    class SlowRequest(ChatRequest):
        @field_validator("model")
        @classmethod
        def validate_model(cls, value):
            now[0] += 0.3
            return value

    async def receive():
        now[0] += 0.1
        return {"type": "http.request", "body": b'{"model":"test"}'}

    request = TimingRequest(
        {"type": "http", "headers": [(b"debug-timing", b"true")]}, receive
    )
    data = await request.json()
    parsed = SlowRequest.model_validate(data)
    assert parsed.model == "test"
    assert await request.json() == data
    assert await request.body() == b'{"model":"test"}'
    spans = request.state.debug_spans
    assert [span["name"] for span in spans] == [
        "request_body_read",
        "prompt_parsing",
    ]
    assert [span["start_ms"] for span in spans] == pytest.approx([0, 100])
    assert [span["duration_ms"] for span in spans] == pytest.approx([100, 200])


@pytest.mark.parametrize(
    "body,status,expected",
    [
        (
            '{"model":"test"}',
            200,
            {"request_body_read", "prompt_parsing"},
        ),
        (
            '{"model":[]}',
            422,
            {"request_body_read", "prompt_parsing"},
        ),
        ("{broken", 422, {"request_body_read", "prompt_parsing"}),
    ],
)
@pytest.mark.parametrize("debug", [False, True])
def test_route_instruments_success_and_failures(mocker, body, status, expected, debug):
    app = FastAPI()
    register_middleware(app)
    log = mocker.patch("mlpa.core.request_timings.logger")

    @app.post("/chat")
    async def chat(payload: ChatRequest, request: Request):
        # The route request and dependency share the middleware's timing state.
        with measure("handler", request):
            return {"model": payload.model}

    with TestClient(app) as client:
        response = client.post(
            "/chat",
            content=body,
            headers={
                "content-type": "application/json",
                "debug-timing": str(debug).lower(),
            },
        )
    assert response.status_code == status
    if debug:
        names = {span["name"] for span in log.bind.call_args.kwargs["spans"]}
        assert expected <= names
        assert ("spans" in response.json()) is (status == 200)
    else:
        log.bind.assert_not_called()
        assert "spans" not in response.json()
