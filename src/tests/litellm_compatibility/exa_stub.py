"""Fake Exa API that fails every request with a documented Exa error body.
Stdlib only: runs as the `exa_stub` service in litellm_docker_compose.yaml."""

import json
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = 8080
STATUS = 503
BODY = json.dumps(
    {
        "requestId": "exa-stub",
        "error": "Exa is temporarily over capacity.",
        "tag": "SERVICE_OVERLOADED",
    }
).encode()


class ExaErrorHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.send_response(STATUS)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", PORT), ExaErrorHandler).serve_forever()
