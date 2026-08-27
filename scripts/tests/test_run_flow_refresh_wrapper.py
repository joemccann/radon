"""Tests for scripts/run_flow_refresh.sh — hourly scanner/flow/discover producer.

2026-08-24 19:00Z page 304b0d7f: all three POSTs got instant HTTP 502 while
/health/lite stayed 200 — ``Subprocess capacity exhausted (3 active, lane
cap 3, hard cap 4)``. The wrapper treated 502 as indeterminate, skipped the
direct fallback (correct), and exited 1 with no retry, so the oneshot paged
P1. Same class as signals-refresh-capacity-502 / R-170.
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

SCAN_PATH = "/scan"
FLOW_PATH = "/flow-analysis"
DISCOVER_PATH = "/discover"


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
    src = Path(__file__).resolve().parents[1] / "run_flow_refresh.sh"
    dst = scripts_dir / "run_flow_refresh.sh"
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
            # The health heredoc is the only other `python -` the wrapper runs;
            # it must reach a real interpreter so the row is observable.
            if [ -n "${{RADON_FLOW_HEALTH_STATE:-}}" ]; then
                exec /usr/bin/env python3 "$@"
            fi
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
            import sys
            print("{marker}")
            sys.exit(0)
            """
        ),
        encoding="utf-8",
    )


def _stage_recording_python(
    bin_dir: Path,
    record_dir: Path,
    *,
    trading_day: bool = True,
    fallback_sleep: int = 0,
) -> Path:
    """A python that RECORDS what the wrapper asks it to do.

    Three call shapes reach it, and each is a behaviour some test used to
    assert by grepping the wrapper's text:
      * `python -` with the market-state probe on stdin  -> answers yes/no
      * `python -` with the health heredoc on stdin       -> health.log
      * `python scripts/<scan>.py <args>`                 -> argv.log
    ``fallback_sleep`` makes the fallback scan outlast its budget, so a
    fallback that is not wrapped in `timeout` hangs the run instead of
    failing it.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    record_dir.mkdir(parents=True, exist_ok=True)
    py = bin_dir / "python3.13"
    _executable(
        py,
        textwrap.dedent(
            f"""\
            #!/bin/bash
            if [ "$1" = "-" ]; then
                body="$(cat)"
                case "$body" in
                    *market_calendar*)
                        echo "{'yes' if trading_day else 'no'}"
                        exit 0
                        ;;
                    *write_service_health_http*)
                        printf '%s\\t%s\\n' \\
                            "${{RADON_FLOW_HEALTH_STATE:-}}" \\
                            "${{RADON_FLOW_HEALTH_MESSAGE:-}}" \\
                            >> "{record_dir}/health.log"
                        exit 0
                        ;;
                esac
                exit 0
            fi
            printf '%s\\n' "$*" >> "{record_dir}/argv.log"
            if [ "{fallback_sleep}" -gt 0 ]; then sleep "{fallback_sleep}"; fi
            exit 0
            """
        ),
    )
    return py


def _recorded(record_dir: Path, name: str) -> list[str]:
    log = record_dir / name
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


class _FastApiStub:
    """Records scan POSTs.

    ``fail_paths`` reply with ``fail_status`` forever. ``flaky_paths`` reply
    with ``fail_status`` on the first hit per path, then 200 — models the
    top-of-hour subprocess slot-cap 502 that clears once a peer finishes.
    """

    def __init__(
        self,
        port: int,
        fail_paths: frozenset[str] = frozenset(),
        flaky_paths: frozenset[str] = frozenset(),
        fail_status: int = 500,
        fail_body: bytes = b'{"detail": "stub failure"}',
    ) -> None:
        self.calls: list[str] = []
        # With the query string: `force=true` is the difference between a
        # scan and a cache-served no-op, and it is invisible in `calls`.
        self.raw_calls: list[str] = []
        self.hits: dict[str, int] = {}
        self._fail_paths = fail_paths
        self._flaky_paths = flaky_paths
        self._fail_status = fail_status
        # The wrapper classifies a shed from the BODY, not the status: the API
        # answers 502 for every failure class (R-221).
        self._fail_body = fail_body
        self._server = HTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 — std lib signature
                path = self.path.split("?", 1)[0]
                recorder.calls.append(path)
                recorder.raw_calls.append(self.path)
                recorder.hits[path] = recorder.hits.get(path, 0) + 1
                hit = recorder.hits[path]
                if path in recorder._fail_paths:
                    code = recorder._fail_status
                elif path in recorder._flaky_paths and hit == 1:
                    code = recorder._fail_status
                else:
                    code = 200
                body = b'{"ok": true}' if code == 200 else recorder._fail_body
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

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


def _run(
    repo_dir: Path,
    python_bin: Path,
    fastapi_port: int,
    *,
    retries: int | None = None,
    delay: int | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "RADON_PYTHON_BIN": str(python_bin),
        "RADON_FLOW_REFRESH_FASTAPI_PORT": str(fastapi_port),
    }
    if retries is not None:
        env["RADON_FLOW_REFRESH_RETRIES"] = str(retries)
    if delay is not None:
        env["RADON_FLOW_REFRESH_RETRY_DELAY_SECS"] = str(delay)
    return subprocess.run(
        ["bash", str(repo_dir / "scripts" / "run_flow_refresh.sh")],
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
    (repo_dir / "logs").mkdir(exist_ok=True)
    return repo_dir


def test_wrapper_posts_three_flow_tabs_each_forced(tmp_path: Path) -> None:
    """T-232: run the wrapper and read the SERVER's log, not the script's text.

    `'run_one "scanner" "/scan?force=true"' in source` survives every refactor
    that stops the line executing — an early `return`, a guard that never
    matches, a renamed function — and `force=true` is what separates a real
    scan from a cache-served no-op, so the hourly UW spend this job exists for
    can stop happening with the assertion still green.
    """
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin")
    port = _free_port()
    stub = _FastApiStub(port)
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port)
    finally:
        stub.stop()

    assert result.returncode == 0, result.stderr or result.stdout
    assert stub.raw_calls == [
        "/scan?force=true",
        "/flow-analysis?force=true",
        "/discover?force=true",
    ], stub.raw_calls


def test_the_direct_fallback_runs_the_cheap_discover_scan(tmp_path: Path) -> None:
    """T-232: the fallback ARGV, observed, not `"--dp-pages 2" in source`.

    Nothing listening on the port, so curl exits 7 and the wrapper runs the
    scans directly. The scoring flags are the whole point of the fallback
    branch: `discover.py` with no `--min-alerts/--dp-pages` walks the full
    tape and cannot finish inside the unit's budget.
    """
    repo_dir = _repo(tmp_path)
    record_dir = tmp_path / "recorded"
    python_bin = _stage_recording_python(tmp_path / "bin", record_dir)

    result = _run(repo_dir, python_bin, _free_port())

    assert result.returncode == 0, result.stderr or result.stdout
    assert _recorded(record_dir, "argv.log") == [
        "scripts/scanner.py --top 25",
        "scripts/flow_analysis.py",
        "scripts/discover.py --min-alerts 3 --dp-pages 2",
    ], _recorded(record_dir, "argv.log")


def test_wrapper_posts_all_three_when_fastapi_reachable(tmp_path: Path) -> None:
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin")
    _stage_scanner_stub(repo_dir / "scripts", "scanner.py", "no-fallback")
    _stage_scanner_stub(repo_dir / "scripts", "flow_analysis.py", "no-fallback")
    _stage_scanner_stub(repo_dir / "scripts", "discover.py", "no-fallback")

    port = _free_port()
    stub = _FastApiStub(port)
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port)
    finally:
        stub.stop()

    assert result.returncode == 0, result.stderr or result.stdout
    assert sorted(stub.calls) == sorted([SCAN_PATH, FLOW_PATH, DISCOVER_PATH]), stub.calls
    assert "fallback" not in (result.stdout + result.stderr).lower()


def test_http_502_then_ok_retries_without_direct_fallback(tmp_path: Path) -> None:
    """2026-08-24 19:00Z: all three flow POSTs got instant 502 while /health
    stayed 200 — ``Subprocess capacity exhausted``. A later POST in the same
    minute must succeed; the direct fallback must never launch.
    """
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin")
    _stage_scanner_stub(repo_dir / "scripts", "scanner.py", "no-fallback")
    _stage_scanner_stub(repo_dir / "scripts", "flow_analysis.py", "no-fallback")
    _stage_scanner_stub(repo_dir / "scripts", "discover.py", "no-fallback")

    port = _free_port()
    stub = _FastApiStub(
        port,
        flaky_paths=frozenset({SCAN_PATH, FLOW_PATH, DISCOVER_PATH}),
        fail_status=502,
        # The body is the shed signal now: the API answers 502 for EVERY
        # failure class, so status alone cannot tell a capacity shed from a
        # scanner traceback (R-221). This is the 2026-08-24 body verbatim.
        fail_body=b'{"detail": "Subprocess capacity exhausted (3 active, lane cap 3, hard cap 4)"}',
    )
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port, retries=2, delay=1)
    finally:
        stub.stop()

    assert result.returncode == 0, result.stderr or result.stdout
    assert stub.hits.get(SCAN_PATH, 0) == 2, stub.hits
    assert stub.hits.get(FLOW_PATH, 0) == 2, stub.hits
    assert stub.hits.get(DISCOVER_PATH, 0) == 2, stub.hits
    combined = (result.stdout + result.stderr).lower()
    assert "retry" in combined
    assert "fallback" not in combined
    assert "no-fallback" not in combined


def test_http_503_then_ok_retries(tmp_path: Path) -> None:
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin")
    _stage_scanner_stub(repo_dir / "scripts", "scanner.py", "no-fallback")
    _stage_scanner_stub(repo_dir / "scripts", "flow_analysis.py", "no-fallback")
    _stage_scanner_stub(repo_dir / "scripts", "discover.py", "no-fallback")

    port = _free_port()
    stub = _FastApiStub(
        port,
        flaky_paths=frozenset({SCAN_PATH}),
        fail_status=503,
        # See the 502 case above: the body carries the shed signal (R-221).
        fail_body=b'{"detail": "Subprocess capacity exhausted (3 active, lane cap 3)"}',
    )
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port, retries=2, delay=1)
    finally:
        stub.stop()

    assert result.returncode == 0, result.stderr or result.stdout
    assert stub.hits.get(SCAN_PATH, 0) == 2, stub.hits


def test_http_500_does_not_retry(tmp_path: Path) -> None:
    """Hard 5xx that is not shedding must still fail the unit once."""
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin")
    port = _free_port()
    stub = _FastApiStub(port, fail_paths=frozenset({SCAN_PATH}), fail_status=500)
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port, retries=2, delay=1)
    finally:
        stub.stop()

    assert result.returncode != 0
    assert stub.hits.get(SCAN_PATH, 0) == 1, stub.hits
    assert "retry" not in (result.stdout + result.stderr).lower()


def test_persistent_502_sheds_without_direct_fallback(tmp_path: Path) -> None:
    """R-170: a persistent 502 is the general subprocess lane being FULL.

    RETRY_LIMIT(2) x RETRY_DELAY(8) cannot clear a hold of minutes; the
    oneshot must exit 0 (shed) so the unit watchdog does not page P1 hourly
    for a condition no retry can fix. Never launch the direct fallback.
    """
    repo_dir = _repo(tmp_path)
    python_bin = _stage_python(tmp_path / "bin")
    _stage_scanner_stub(repo_dir / "scripts", "scanner.py", "scanner-direct")
    _stage_scanner_stub(repo_dir / "scripts", "flow_analysis.py", "flow-direct")
    _stage_scanner_stub(repo_dir / "scripts", "discover.py", "discover-direct")

    port = _free_port()
    stub = _FastApiStub(
        port,
        fail_paths=frozenset({SCAN_PATH, FLOW_PATH, DISCOVER_PATH}),
        fail_status=502,
        # The body is the shed signal now: the API answers 502 for EVERY
        # failure class, so status alone cannot tell a capacity shed from a
        # scanner traceback (R-221). This is the 2026-08-24 body verbatim.
        fail_body=b'{"detail": "Subprocess capacity exhausted (3 active, lane cap 3, hard cap 4)"}',
    )
    stub.start()
    try:
        result = _run(repo_dir, python_bin, port, retries=2, delay=1)
    finally:
        stub.stop()

    assert result.returncode == 0
    combined = (result.stdout + result.stderr).lower()
    assert "shed" in combined and "capacity" in combined
    assert combined.count("retry") >= 2
    assert "fallback" not in combined
    assert "scanner-direct" not in combined
    assert stub.hits.get(SCAN_PATH, 0) >= 3, stub.hits
