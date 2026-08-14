"""run_portfolio_refresh.sh — deploy-window connection-refused retry.

2026-08-05: the every-minute portfolio-sync timer landed its POST inside
the seconds-wide radon-api restart gap during two deploys (16:47:04Z,
20:45:02Z). curl exited 7 (connection refused) instantly, the oneshot
failed, and the unit watchdog paged — pure deploy collateral. The wrapper
must retry connection-refused a bounded number of times so a restart gap
heals, while a genuinely down FastAPI still fails after the retries.
"""

import http.server
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO_ROOT / "scripts" / "run_portfolio_refresh.sh"

FAKE_PYTHON = """#!/bin/bash
# Stand-in for the wrapper's python probes: succeed the `-c "import sys"`
# resolver check, answer "yes" to the trading-day heredoc so tests are
# calendar-independent.
if [ "$1" = "-c" ]; then exit 0; fi
echo yes
"""


@pytest.fixture
def fake_python(tmp_path: Path) -> Path:
    shim = tmp_path / "fake-python"
    shim.write_text(FAKE_PYTHON)
    shim.chmod(0o755)
    return shim


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


def _flaky_status_handler(fail_code: int):
    state = {"hits": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            state["hits"] += 1
            code = fail_code if state["hits"] == 1 else 200
            self.send_response(code)
            self.end_headers()

        def log_message(self, *args):
            pass

    return Handler, state


def run_script(port: int, fake_python: Path, retries: int = 2,
               delay: int = 1) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=REPO_ROOT,
        env={
            "PATH": "/usr/bin:/bin:/usr/sbin",
            "HOME": str(fake_python.parent),
            "RADON_PYTHON_BIN": str(fake_python),
            "RADON_PORTFOLIO_REFRESH_FASTAPI_PORT": str(port),
            "RADON_PORTFOLIO_REFRESH_RETRIES": str(retries),
            "RADON_PORTFOLIO_REFRESH_RETRY_DELAY_SECS": str(delay),
        },
        capture_output=True, text=True, timeout=60,
    )


class TestDeployWindowRetry:
    def test_server_up_after_first_refusal_succeeds(self, fake_python: Path):
        """The deploy restart window: first POST refused, API back before
        the retry — the run must succeed instead of failing the unit."""
        port = free_port()
        holder: dict = {}

        def start_late():
            # Bind AFTER the script's first attempt — HTTPServer listens
            # at construction, so building it early defeats the test.
            time.sleep(0.5)
            holder["server"] = http.server.HTTPServer(("127.0.0.1", port), _OkHandler)
            holder["server"].serve_forever()

        threading.Thread(target=start_late, daemon=True).start()
        try:
            result = run_script(port, fake_python, retries=3, delay=1)
        finally:
            if "server" in holder:
                holder["server"].shutdown()
        assert result.returncode == 0, result.stderr
        assert "retry" in (result.stdout + result.stderr).lower()

    def test_persistent_refusal_still_fails_with_exit_7(self, fake_python: Path):
        """A genuinely down FastAPI must still fail (bounded retries),
        preserving curl's exit status for the unit alert."""
        port = free_port()
        result = run_script(port, fake_python, retries=2, delay=1)
        assert result.returncode == 7
        combined = (result.stdout + result.stderr).lower()
        assert combined.count("retry") >= 2

    def test_healthy_server_succeeds_without_retry(self, fake_python: Path):
        port = free_port()
        server = http.server.HTTPServer(("127.0.0.1", port), _OkHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = run_script(port, fake_python)
        finally:
            server.shutdown()
        assert result.returncode == 0, result.stderr
        assert "retry" not in (result.stdout + result.stderr).lower()

    def test_http_502_then_ok_retries(self, fake_python: Path):
        """2026-08-14 17:00Z: /portfolio/sync returned 502 instantly while
        /health stayed 200 (top-of-hour scan pile-up / slot cap). curl -f
        exits 22, the oneshot paged, and the wrapper only retried exit 7.
        A later POST in the same minute must succeed."""
        port = free_port()
        handler, state = _flaky_status_handler(502)
        server = http.server.HTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = run_script(port, fake_python, retries=2, delay=1)
        finally:
            server.shutdown()
        assert result.returncode == 0, result.stderr
        assert state["hits"] == 2
        assert "retry" in (result.stdout + result.stderr).lower()

    def test_http_503_then_ok_retries(self, fake_python: Path):
        port = free_port()
        handler, state = _flaky_status_handler(503)
        server = http.server.HTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = run_script(port, fake_python, retries=2, delay=1)
        finally:
            server.shutdown()
        assert result.returncode == 0, result.stderr
        assert state["hits"] == 2

    def test_http_400_does_not_retry(self, fake_python: Path):
        port = free_port()
        handler, state = _flaky_status_handler(400)
        server = http.server.HTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = run_script(port, fake_python, retries=2, delay=1)
        finally:
            server.shutdown()
        assert result.returncode == 22
        assert state["hits"] == 1

    def test_persistent_502_still_fails_with_exit_22(self, fake_python: Path):
        port = free_port()

        class Always502(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.send_response(502)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", port), Always502)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            result = run_script(port, fake_python, retries=2, delay=1)
        finally:
            server.shutdown()
        assert result.returncode == 22
        combined = (result.stdout + result.stderr).lower()
        assert combined.count("retry") >= 2
