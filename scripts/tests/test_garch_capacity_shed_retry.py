"""radon-garch capacity shed must wait and retry, never duplicate, never mislabel.

2026-09-01 14:00:12Z page 776ea756: radon-garch.service Result=exit-code,
NRestarts=0. Journal:

  POST .../garch-convergence/scan?preset=largecaps
  GARCH FastAPI outcome indeterminate (curl=0, http=502); not launching duplicate

/health/lite stayed 200 / authenticated. LEAP (same 14:00 window) POSTed at
14:01:16Z and completed OK at 14:02:33Z — the lane cleared within ~2 minutes.
The garch wrapper treated the shed as indeterminate, exited 1 once, and the
unit watchdog paged P1. Next timer 17:02 UTC; garch.json stayed on the prior
run.

Same class as leap-capacity-502 (2026-08-27 page 4e9ebc66). Leap gained the
R-221 body-marker wait; garch did not. Short retries must actually wait for
a slot. Do not treat every 502 as a shed.
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

SCRIPTS = Path(__file__).resolve().parents[1]
WRAPPER = "run_garch_refresh.sh"
SCANNER = "garch_convergence.py"
PATH = "/garch-convergence/scan"
SHED_BODY = b'{"detail": "Subprocess capacity exhausted (3 active, lane cap 3, hard cap 4)"}'
SCRIPT_FAIL_BODY = b'{"detail": "Script garch_convergence.py failed (code 1)"}'
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
    # T-283: the retry ladder's wait is RECORDED, not spent.
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
        "RADON_GARCH_REFRESH_FASTAPI_PORT": str(port),
        "RADON_GARCH_SHED_WAIT_SECS": shed_wait,
        "RADON_GARCH_REFRESH_RETRY_DELAY_SECS": delay,
        "RADON_GARCH_SLEEP_CMD": str(repo.parent / "bin" / "sleep-recorder"),
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
    """2026-09-01: shed at 14:00:12Z, peer lane free by 14:02 — retry must win."""
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
    """Oneshot: after the shed wait, still fail (next slot is the 17:00 timer)."""
    marker = tmp_path / "direct-ran"
    repo, py = _repo(tmp_path, marker)
    port = _free_port()

    with _Stub(port, always_status=502, fail_body=SHED_BODY) as stub:
        result = _run(repo, py, port, shed_wait="3", delay="1")

    assert result.returncode != 0, result.stdout + result.stderr
    # The ladder is exact: a 3s budget at a 1s delay buys three waits and
    # a fourth POST, then gives up.
    assert stub.calls == [PATH] * 4, stub.calls
    assert _sleeps(tmp_path) == [1, 1, 1], _sleeps(tmp_path)
    assert not marker.exists()
    combined = (result.stdout + result.stderr).lower()
    assert "capacity" in combined or "shed" in combined
    assert "fallback" not in combined
