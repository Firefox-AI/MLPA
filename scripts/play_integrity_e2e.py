"""
E2E Play Integrity flow against a running MLPA server.
Requires a real Play Integrity token and configured service account file on the server.
"""

import argparse
import json
import os
from typing import Optional

import httpx

from mlpa.core.config import env

DEFAULT_BASE_URL = f"http://0.0.0.0:{env.PORT or 8080}"
DEFAULT_SERVICE_TYPE = "ai"


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _require_value(value: Optional[str], name: str) -> str:
    if value:
        return value
    raise SystemExit(f"Missing required value for {name}.")


def run(args: argparse.Namespace) -> None:
    integrity_token = _require_value(
        args.integrity_token or os.getenv("MLPA_PLAY_INTEGRITY_TOKEN"),
        "integrity_token",
    )
    user_id = _require_value(
        args.user_id or os.getenv("MLPA_PLAY_USER_ID"),
        "user_id",
    )

    verify_response = httpx.post(
        f"{args.base_url}/verify/play",
        json={"integrity_token": integrity_token, "user_id": user_id},
        timeout=args.timeout_s,
    )
    verify_response.raise_for_status()
    access_token = verify_response.json().get("access_token")
    if not access_token:
        raise SystemExit("No access_token returned from /verify/play.")

    headers = {
        "authorization": f"Bearer {access_token}",
        "use-play-integrity": "true",
        "service-type": args.service_type,
    }
    payload = {
        "model": args.model or env.MODEL_NAME,
        "messages": [{"role": "user", "content": args.message}],
        "stream": args.stream,
    }

    if args.stream:
        with httpx.stream(
            "POST",
            f"{args.base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=args.timeout_s,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line:
                    print(line)
    else:
        response = httpx.post(
            f"{args.base_url}/v1/chat/completions",
            headers=headers,
            json=payload,
            timeout=args.timeout_s,
        )
        response.raise_for_status()
        _print_json(response.json())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="E2E Play Integrity verification + chat completion."
    )
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Verify and request a completion.")
    run_parser.add_argument("--integrity-token", dest="integrity_token")
    run_parser.add_argument("--user-id", dest="user_id")
    run_parser.add_argument(
        "--base-url", dest="base_url", default="http://localhost:8080"
    )
    run_parser.add_argument("--timeout-s", dest="timeout_s", type=int, default=30)
    run_parser.add_argument(
        "--service-type", dest="service_type", default=DEFAULT_SERVICE_TYPE
    )
    run_parser.add_argument("--model", dest="model")
    run_parser.add_argument("--message", dest="message")
    run_parser.set_defaults(func=run)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if not getattr(args, "command", None):
        parser.print_help()
        raise SystemExit(2)
    args.func(args)


if __name__ == "__main__":
    main()
