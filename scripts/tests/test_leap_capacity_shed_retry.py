"""radon-leap capacity shed must wait and retry, never duplicate, never mislabel.

2026-08-27 14:00:20Z page 4e9ebc66: radon-leap.service Result=exit-code,
NRestarts=0. Journal:

  POST .../leap/scan?preset=largecaps&min_gap=10
  LEAP FastAPI outcome indeterminate (curl=0, http=502); not launching duplicate

radon-api in the same second:

  Subprocess capacity exhausted for leap_scanner_uw.py
    (3 active, lane cap 3, hard cap 4)
  POST /leap/scan?... 502

/health/lite stayed 200 / authenticated. GARCH (same 14:00 window) POSTed at
14:02:02Z and completed OK — the lane cleared within ~2 minutes. The leap
wrapper treated the shed as indeterminate, exited 1 once, and the unit
watchdog paged P1. Next timer is ~24h; leap.json stayed on the prior day.

Same class as signals-refresh-capacity-502 / flow-refresh-capacity-502, but
daily: short retries must actually wait for a slot (R-221 body marker; do not
treat every 502 as a shed).
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

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
WRAPPER = "run_leap_refresh.sh"
SCANNER = "leap_scanner_uw.py"
PATH = "/leap/scan"
SHED_BODY = b'{"detail": "Subprocess capacity exhausted (3 active, lane cap 3, hard cap 4)"}'
SCRIPT_FAIL_BODY = b'{"detail": "Script leap_scanner_uw.py failed (code 1)"}'
SLEEP_LOG = "sleeps.log"


def _sleeps(tmp_path: Path) -> list[int]:
    """Seconds the ladder ASKED to wait, in order (T-283)."""
    log = tmp_path / SLEEP_LOG
    return [int(x) for x in log.read_text().split()] if log.exists() else []


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


class _Stub:
    """FastAPI stand-in.

    ``fail_count`` capacity-shed 502s, then 200. ``always_status`` forces every
    response. ``fail_body`` is the 502 payload (shed marker vs script failure).
    """

    def __init__(
        self,
        port: int,
        *,
        fail_count: int = 0,
        always_status: int | None = None,
        fail_body: bytes = SHED_BODY,
    ):
        self.calls: list[str] = []
        self._fail_count = fail_count
        self._always_status = always_status
        self._fail_body = fail_body
        self._hits = 0
        self._server = HTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler(self):
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                recorder.calls.append(self.path.split("?", 1)[0])
                recorder._hits += 1
                if recorder._always_status is not None:
                    status = recorder._always_status
                    body = (
                        recorder._fail_body
                        if status >= 400
                        else b'{"ok": true}'
                    )
                elif recorder._hits <= recorder._fail_count:
                    status = 502
                    body = recorder._fail_body
                else:
                    status = 200
                    body = b'{"ok": true}'
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_a):
                return

        return Handler

    def __enter__(self):
        self._thread.start()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(
                    ("127.0.0.1", self._server.server_port), timeout=0.1
                ):
                    return self
            except OSError:
                time.sleep(0.01)
        return self

    def __exit__(self, *_a):
        self._server.shutdown()
        self._server.server_close()


def _repo(tmp_path: Path, marker: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir(parents=True)
    (repo / "data").mkdir()
    shutil.copy2(SCRIPTS / WRAPPER, scripts_dir / WRAPPER)
    (scripts_dir / WRAPPER).chmod(0o755)

    _executable(
        scripts_dir / SCANNER,
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import pathlib
            pathlib.Path({str(marker)!r}).write_text("direct\\n")
            print("{{}}")
            """
        ),
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # T-283: the retry ladder's wait is RECORDED, not spent. Driving it off
    # real sleeps inside a `SECONDS` deadline made these cases load-bound —
    # two of the three reds in the 2026-08-29 gate at load ~200 were here,
    # both `assert 1 >= 2` because the budget was gone before the first retry.
    _executable(
        bin_dir / "sleep-recorder",
        textwrap.dedent(
            f"""\
            #!/bin/bash
            echo "$1" >> {str(tmp_path / SLEEP_LOG)!r}
            """
        ),
    )
    py = bin_dir / "python3.13"
    _executable(
        py,
        textwrap.dedent(
            """\
            #!/bin/bash
            if [ "$1" = "-" ]; then cat >/dev/null; echo yes; exit 0; fi
            if [ "$1" = "-c" ]; then exit 0; fi
            exec /usr/bin/env python3 "$@"
            """
        ),
    )
    return repo, py


def _run(
    repo: Path,
    py: Path,
    port: int,
    *,
    shed_wait: str = "30",
    delay: str = "1",
) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "RADON_PYTHON_BIN": str(py),
        "RADON_LEAP_REFRESH_FASTAPI_PORT": str(port),
        "RADON_LEAP_SHED_WAIT_SECS": shed_wait,
        "RADON_LEAP_REFRESH_RETRY_DELAY_SECS": delay,
        "RADON_LEAP_SLEEP_CMD": str(repo.parent / "bin" / "sleep-recorder"),
    }
    return subprocess.run(
        ["bash", str(repo / "scripts" / WRAPPER)],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )


def test_capacity_502_then_ok_retries_without_direct_fallback(tmp_path: Path) -> None:
    """2026-08-27: shed at 14:00:20Z, peer lane free by 14:02 — retry must win."""
    marker = tmp_path / "direct-ran"
    repo, py = _repo(tmp_path, marker)
    port = _free_port()

    with _Stub(port, fail_count=1) as stub:
        result = _run(repo, py, port, shed_wait="20", delay="1")

    assert result.returncode == 0, result.stdout + result.stderr
    assert stub.calls == [PATH, PATH], stub.calls
    assert _sleeps(tmp_path) == [1], _sleeps(tmp_path)
    assert not marker.exists(), "direct fallback must not run after a capacity shed"
    combined = (result.stdout + result.stderr).lower()
    assert "retry" in combined
    assert "capacity" in combined or "shed" in combined
    assert "indeterminate" not in combined
    assert "fallback" not in combined


def test_script_failed_502_does_not_retry_as_shed(tmp_path: Path) -> None:
    """R-221: FastAPI maps every script failure to HTTP 502; body is the tell."""
    marker = tmp_path / "direct-ran"
    repo, py = _repo(tmp_path, marker)
    port = _free_port()

    with _Stub(port, always_status=502, fail_body=SCRIPT_FAIL_BODY) as stub:
        result = _run(repo, py, port, shed_wait="20", delay="1")

    assert result.returncode != 0, result.stdout + result.stderr
    assert stub.calls == [PATH], stub.calls
    assert _sleeps(tmp_path) == [], _sleeps(tmp_path)
    assert not marker.exists()
    combined = (result.stdout + result.stderr).lower()
    assert "indeterminate" in combined
    assert "retry" not in combined


def test_persistent_capacity_shed_no_duplicate_still_fails(tmp_path: Path) -> None:
    """Daily oneshot: after the shed wait, still fail (next slot is ~24h)."""
    marker = tmp_path / "direct-ran"
    repo, py = _repo(tmp_path, marker)
    port = _free_port()

    with _Stub(port, always_status=502, fail_body=SHED_BODY) as stub:
        result = _run(repo, py, port, shed_wait="3", delay="1")

    assert result.returncode != 0, result.stdout + result.stderr
    # The ladder is now exact: a 3s budget at a 1s delay buys three waits and
    # a fourth POST, then gives up. Was `>= 2` because the count was a
    # function of how fast the box happened to be.
    assert stub.calls == [PATH] * 4, stub.calls
    assert _sleeps(tmp_path) == [1, 1, 1], _sleeps(tmp_path)
    assert not marker.exists()
    combined = (result.stdout + result.stderr).lower()
    assert "capacity" in combined or "shed" in combined
    assert "fallback" not in combined
