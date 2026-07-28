"""Tests for scripts/run_vcg_refresh.sh — the systemd / launchd entry
point that gives vcg-scan an autonomous market-hours cadence.

The wrapper has three responsibilities the banner depends on:

  1. Skip on weekends / market holidays (no point firing a scan when
     vcg_scan.py would just no-op).
  2. POST through the local FastAPI ``/vcg/scan`` endpoint when the
     server is reachable so the cache + service_health row + Turso
     dual-write all happen via the same code path the browser uses.
  3. Fall back to invoking vcg_scan.py directly when FastAPI is
     unreachable, so a one-off cron / manual run still updates the
     cache file.

These tests stub FastAPI with a one-shot Python HTTP server and stub
``vcg_scan.py`` with a deterministic shell shim, then assert the
wrapper's stdout/exit code reflect the path taken.
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
    """Copy run_vcg_refresh.sh into ``repo_dir/scripts/`` and return its path."""
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    src = Path(__file__).resolve().parents[1] / "run_vcg_refresh.sh"
    dst = scripts_dir / "run_vcg_refresh.sh"
    shutil.copy2(src, dst)
    dst.chmod(dst.stat().st_mode | stat.S_IXUSR)
    _stage_python_bin_lib(scripts_dir)
    return dst


def _stage_python_bin_lib(scripts_dir: Path) -> None:
    """Wrappers source interpreter resolution from scripts/lib/python_bin.sh."""
    lib_dir = scripts_dir / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        Path(__file__).resolve().parents[1] / "lib" / "python_bin.sh",
        lib_dir / "python_bin.sh",
    )


def _stage_python_with_market_open(bin_dir: Path) -> Path:
    """Stub python3.13 that pretends today is always a trading day."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = bin_dir / "python3.13"
    _executable(
        py,
        textwrap.dedent(
            """\
            #!/bin/bash
            # The wrapper invokes python in two distinct modes:
            #   1) `python - <<PY` heredoc probe — answers "yes" to is-trading-day.
            #   2) `python scripts/vcg_scan.py --json` — emits a stub payload.
            if [ "$1" = "-" ]; then
                cat >/dev/null
                echo "yes"
                exit 0
            fi
            if [ "$1" = "-c" ]; then
                # ib_insync availability probe — succeed.
                exit 0
            fi
            # Forward any other invocation to the system python so stub
            # scripts (vcg_scan.py) still execute.
            exec /usr/bin/env python3 "$@"
            """
        ),
    )
    return py


def _stage_vcg_scan_stub(scripts_dir: Path, marker: str) -> None:
    """Drop a fake vcg_scan.py that emits a recognisable JSON payload."""
    stub = scripts_dir / "vcg_scan.py"
    stub.write_text(
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
    """Single-shot HTTP server that records POST /vcg/scan calls."""

    def __init__(self, port: int) -> None:
        self.calls: list[str] = []
        self._server = HTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — std lib signature
                recorder.calls.append(self.path)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"scan_time": "real"}')

            def log_message(self, *_args: object) -> None:  # silence
                return

        return Handler

    def start(self) -> None:
        self._thread.start()
        # Wait until the port is actually accepting connections.
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
        "RADON_VCG_REFRESH_FASTAPI_PORT": str(fastapi_port),
    }
    return subprocess.run(
        ["bash", str(repo_dir / "scripts" / "run_vcg_refresh.sh")],
        cwd=repo_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_wrapper_posts_to_fastapi_when_reachable(tmp_path: Path) -> None:
    """Happy path: FastAPI is up, wrapper POSTs /vcg/scan and exits 0."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _stage_wrapper(repo_dir)

    bin_dir = tmp_path / "bin"
    python_bin = _stage_python_with_market_open(bin_dir)
    _stage_vcg_scan_stub(repo_dir / "scripts", marker="fallback-should-not-run")

    port = _free_port()
    stub = _FastApiStub(port)
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port)
    finally:
        stub.stop()

    assert result.returncode == 0, result.stderr or result.stdout
    assert stub.calls == ["/vcg/scan"], stub.calls
    # The wrapper should not have fallen back to the direct invocation.
    assert "fallback" not in (result.stdout + result.stderr).lower()


def test_wrapper_falls_back_to_direct_invocation_when_fastapi_down(tmp_path: Path) -> None:
    """When FastAPI is unreachable, the wrapper still runs vcg_scan.py and
    writes data/vcg.json so a manual run is useful even without the server."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _stage_wrapper(repo_dir)
    (repo_dir / "data").mkdir()

    bin_dir = tmp_path / "bin"
    python_bin = _stage_python_with_market_open(bin_dir)
    _stage_vcg_scan_stub(repo_dir / "scripts", marker="direct-path")

    # Pick a port that nothing is listening on.
    port = _free_port()

    result = _run(repo_dir, python_bin, port)

    assert result.returncode == 0, result.stderr or result.stdout
    vcg_json = repo_dir / "data" / "vcg.json"
    assert vcg_json.exists(), "wrapper must write data/vcg.json on fallback"
    assert "direct-path" in vcg_json.read_text(encoding="utf-8")


def test_wrapper_skips_on_market_holiday(tmp_path: Path) -> None:
    """When the trading-day probe says ``no``, the wrapper exits 0 without
    touching vcg_scan.py or hitting FastAPI."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    _stage_wrapper(repo_dir)

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    py = bin_dir / "python3.13"
    _executable(
        py,
        textwrap.dedent(
            """\
            #!/bin/bash
            if [ "$1" = "-" ]; then
                cat >/dev/null
                echo "no"
                exit 0
            fi
            if [ "$1" = "-c" ]; then
                exit 0
            fi
            exec /usr/bin/env python3 "$@"
            """
        ),
    )
    # vcg_scan.py is intentionally missing — if the wrapper accidentally
    # runs it the test will fail with a clear "stub not found" trace.

    port = _free_port()
    stub = _FastApiStub(port)
    stub.start()
    try:
        result = _run(repo_dir, py, port)
    finally:
        stub.stop()

    assert result.returncode == 0, result.stderr or result.stdout
    assert stub.calls == [], "must not POST to FastAPI on holidays"
    combined = (result.stdout + result.stderr).lower()
    assert "holiday" in combined or "skip" in combined
