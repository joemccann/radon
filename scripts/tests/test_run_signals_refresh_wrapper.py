"""Tests for scripts/run_signals_refresh.sh — the systemd entry point that
gives the dashboard's "Top candidates" panel an autonomous market-hours
cadence.

Both scans behind that panel (theta harvester, 7-step strength confirmation)
had FastAPI endpoints, a Turso mirror and a service_health row, but nothing
ever called them: the newest theta snapshot was a day old and the newest
strength snapshot sixteen days old while the panel rendered them as the
current sample. This wrapper is the missing scheduler.

Responsibilities:

  1. Skip on weekends / market holidays.
  2. POST through the local FastAPI scan endpoints so the cache +
     service_health row + Turso mirror all happen on the same code path a
     manual scan uses.
  3. Run BOTH scans even when the first one fails — the panel's two tabs are
     independent — while still exiting non-zero so systemd records the fault.
  4. Fall back to invoking the scanner scripts directly when FastAPI is
     unreachable so the file cache stays warm.
"""
from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
import textwrap
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

THETA_PATH = "/theta-harvester/scan"
STRENGTH_PATH = "/strength-confirmation/scan"


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    try:
        return sock.getsockname()[1]
    finally:
        sock.close()


def _executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _stage_wrapper(repo_dir: Path) -> Path:
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parents[1] / "run_signals_refresh.sh"
    dst = scripts_dir / "run_signals_refresh.sh"
    shutil.copy2(src, dst)
    dst.chmod(dst.stat().st_mode | stat.S_IXUSR)
    return dst


def _stage_python(bin_dir: Path, *, trading_day: bool = True) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = bin_dir / "python3.13"
    _executable(
        py,
        textwrap.dedent(
            f"""\
            #!/bin/bash
            # Heredoc probe -> trading-day answer. Everything else forwards to
            # the real interpreter so staged scanner stubs still execute.
            if [ "$1" = "-" ]; then
                cat >/dev/null
                echo "{'yes' if trading_day else 'no'}"
                exit 0
            fi
            if [ "$1" = "-c" ]; then
                exit 0
            fi
            exec /usr/bin/env python3 "$@"
            """
        ),
    )
    return py


def _stage_scanner_stub(scripts_dir: Path, name: str, marker: str) -> None:
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / name).write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json, sys
            print(json.dumps({{"scan_time": "stub", "marker": "{marker}"}}))
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )


class _FastApiStub:
    """Records scan POSTs; ``fail_paths`` reply 500 so failure paths are testable."""

    def __init__(self, port: int, fail_paths: frozenset[str] = frozenset()) -> None:
        self.calls: list[str] = []
        self._fail_paths = fail_paths
        self._server = HTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — std lib signature
                path = self.path.split("?", 1)[0]
                recorder.calls.append(path)
                failed = path in recorder._fail_paths
                self.send_response(500 if failed else 200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"scan_time": "real"}')

            def log_message(self, *_args: object) -> None:
                return

        return Handler

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", self._server.server_port), timeout=0.1):
                    return
            except OSError:
                time.sleep(0.01)

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def _run(repo_dir: Path, python_bin: Path, fastapi_port: int) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "RADON_PYTHON_BIN": str(python_bin),
        "RADON_SIGNALS_REFRESH_FASTAPI_PORT": str(fastapi_port),
    }
    return subprocess.run(
        ["bash", str(repo_dir / "scripts" / "run_signals_refresh.sh")],
        cwd=repo_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _repo(tmp_path: Path) -> Path:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _stage_wrapper(repo_dir)
    (repo_dir / "data").mkdir(exist_ok=True)
    return repo_dir


def test_wrapper_posts_both_scans_when_fastapi_reachable(tmp_path: Path) -> None:
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin")
    _stage_scanner_stub(repo_dir / "scripts", "theta_harvester_scanner.py", "no-fallback")
    _stage_scanner_stub(repo_dir / "scripts", "strength_confirmation_scanner.py", "no-fallback")

    port = _free_port()
    stub = _FastApiStub(port)
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port)
    finally:
        stub.stop()

    assert result.returncode == 0, result.stderr or result.stdout
    assert sorted(stub.calls) == sorted([THETA_PATH, STRENGTH_PATH]), stub.calls
    assert "fallback" not in (result.stdout + result.stderr).lower()


def test_strength_scan_still_runs_when_theta_scan_fails(tmp_path: Path) -> None:
    """A theta failure must not silently cost the panel its strength tab."""
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin")
    # No scanner stubs staged: the fallback invocation fails too, so the run
    # is a genuine theta failure while strength succeeds over HTTP.
    port = _free_port()
    stub = _FastApiStub(port, fail_paths=frozenset({THETA_PATH}))
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port)
    finally:
        stub.stop()

    assert STRENGTH_PATH in stub.calls, stub.calls
    assert result.returncode != 0, "a failed scan must surface as a unit failure"


def test_wrapper_falls_back_to_direct_invocation_when_fastapi_down(tmp_path: Path) -> None:
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin")
    _stage_scanner_stub(repo_dir / "scripts", "theta_harvester_scanner.py", "theta-direct")
    _stage_scanner_stub(repo_dir / "scripts", "strength_confirmation_scanner.py", "strength-direct")

    result = _run(repo_dir, python_bin, _free_port())

    assert result.returncode == 0, result.stderr or result.stdout
    combined = result.stdout + result.stderr
    assert "theta-direct" in combined or "fallback" in combined.lower()


def test_wrapper_skips_on_market_holiday(tmp_path: Path) -> None:
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin", trading_day=False)

    port = _free_port()
    stub = _FastApiStub(port)
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port)
    finally:
        stub.stop()

    assert result.returncode == 0, result.stderr or result.stdout
    assert stub.calls == [], "holiday run must not hit FastAPI"
