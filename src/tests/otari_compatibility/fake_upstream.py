"""
A stand-in for the provider behind Otari in otari_docker_compose.yaml, standard
library only.

OpenAI-compatible, so Otari reaches it as a `provider_type: openai` instance:
chat completions, streamed or not, answering MOCK_RESPONSE_TEXT with token usage
so Otari can price the request and record spend.

    python fake_upstream.py --port 9100
"""

import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Must match tests.helpers.MOCK_RESPONSE_TEXT (this file runs outside MLPA's package).
MOCK_RESPONSE_TEXT = "this is a mocked response"


def _usage(messages: list[dict], completion_tokens: int) -> dict:
    prompt_tokens = sum(len(str(m.get("content", "")).split()) for m in messages) + 5
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path.endswith("/models"):
            self._json(200, {"object": "list", "data": []})
        else:
            self._json(404, {"error": {"message": "not found"}})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        request = json.loads(self.rfile.read(length) or b"{}")
        if not self.path.endswith("/chat/completions"):
            self._json(404, {"error": {"message": "not found"}})
            return
        model = request.get("model", "")
        messages = request.get("messages", [])
        completion_tokens = len(MOCK_RESPONSE_TEXT.split())
        if request.get("stream"):
            self._stream(model, messages, completion_tokens)
            return
        self._json(
            200,
            {
                "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": MOCK_RESPONSE_TEXT},
                    }
                ],
                "usage": _usage(messages, completion_tokens),
            },
        )

    def _stream(self, model: str, messages: list[dict], completion_tokens: int) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        base = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
        }
        words = MOCK_RESPONSE_TEXT.split(" ")
        for index, word in enumerate(words):
            delta: dict[str, str] = {
                "content": word + (" " if index < len(words) - 1 else "")
            }
            if index == 0:
                delta["role"] = "assistant"
            chunk = {
                **base,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        done = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
        self.wfile.write(f"data: {json.dumps(done)}\n\n".encode())
        usage = {**base, "choices": [], "usage": _usage(messages, completion_tokens)}
        self.wfile.write(f"data: {json.dumps(usage)}\n\ndata: [DONE]\n\n".encode())
        self.wfile.flush()
        self.close_connection = True


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9100)
    args = parser.parse_args()
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()
