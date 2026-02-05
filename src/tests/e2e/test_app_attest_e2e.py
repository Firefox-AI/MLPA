import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_for_liveness(base_url: str, timeout_s: int = 30) -> None:
    deadline = time.time() + timeout_s
    url = f"{base_url}/health/liveness"
    while time.time() < deadline:
        try:
            response = httpx.get(url, timeout=2)
            if response.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise AssertionError("MLPA did not become live in time.")


def test_app_attest_qa_flow_e2e():
    cert_dir = Path("src/tests/certs")
    cert_dir_exists = cert_dir.exists()
    existing_files = {p.name for p in cert_dir.iterdir()} if cert_dir_exists else set()
    cert_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["APP_ATTEST_QA"] = "true"
    env["APP_ATTEST_QA_CERT_DIR"] = str(cert_dir)
    env["MLPA_DEBUG"] = "false"

    port = _pick_free_port()
    base_url = f"http://127.0.0.1:{port}"
    env["PORT"] = str(port)

    server = None
    try:
        subprocess.run(
            [
                sys.executable,
                "scripts/app_attest_qa/generate_qa_app_attest_certificate.py",
            ],
            check=True,
            env=env,
        )

        server = subprocess.Popen(
            ["mlpa"],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _wait_for_liveness(base_url, timeout_s=60)

        subprocess.run(
            [
                sys.executable,
                "scripts/app_attest_qa/app_attest_qa.py",
                "register",
            ],
            check=True,
            env=env,
        )

        subprocess.run(
            [
                sys.executable,
                "scripts/app_attest_qa/app_attest_qa.py",
                "completion",
            ],
            check=True,
            env=env,
        )
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
        if cert_dir.exists():
            for path in cert_dir.iterdir():
                if path.name not in existing_files:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
            if not cert_dir_exists:
                shutil.rmtree(cert_dir)
