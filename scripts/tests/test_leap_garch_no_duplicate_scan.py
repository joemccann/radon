"""REL-058 / R-144 (P0) — a 502 or a timeout must not launch a duplicate scan.

`run_leap_refresh.sh` and `run_garch_refresh.sh` treat every non-2xx
identically and fall through to a DIRECT scanner invocation. A
`502 Subprocess capacity exhausted` from `_claim_subprocess_slot` — the one
response that means the box is ALREADY running too many scans — therefore
launched the scanner outside `MAX_CONCURRENT_SUBPROCESSES` and outside the
FastAPI cooldown, as did a `curl -m 3610` timeout while FastAPI's child may
still be mid-flight. `run_signals_refresh.sh` got exactly this guard; leap
and garch did not, and `docs/incident-runbook.md` records the duplicate runs
for 2026-08-18/19/20. One duplicate `largecaps` LEAP run is 518 names x 3 UW
GETs, ~3.9% of the daily cap; garch fires 3x per trading day.

Only `curl` exit 7 (connection refused) proves the request was never
accepted, so only that may fall back.
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

WRAPPERS = {
    "leap": {
        "wrapper": "run_leap_refresh.sh",
        "scanner": "leap_scanner_uw.py",
        "port_env": "RADON_LEAP_REFRESH_FASTAPI_PORT",
        "path": "/leap/scan",
    },
    "garch": {
        "wrapper": "run_garch_refresh.sh",
        "scanner": "garch_convergence.py",
        "port_env": "RADON_GARCH_REFRESH_FASTAPI_PORT",
        "path": "/garch-convergence/scan",
    },
}


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
    """FastAPI stand-in. ``status=None`` hangs so curl hits -m and times out."""

    def __init__(self, port: int, status: int | None):
        self.calls: list[str] = []
        self._status = status
        self._server = HTTPServer(("127.0.0.1", port), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _handler(self):
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                recorder.calls.append(self.path.split("?", 1)[0])
                if recorder._status is None:
                    time.sleep(10)
                    return
                self.send_response(recorder._status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"detail": "Subprocess capacity exhausted"}')

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


def _repo(tmp_path: Path, spec: dict, marker: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir(parents=True)
    (repo / "data").mkdir()
    shutil.copy2(SCRIPTS / spec["wrapper"], scripts_dir / spec["wrapper"])
    (scripts_dir / spec["wrapper"]).chmod(0o755)

    # The scanner stub records that a DIRECT invocation happened.
    _executable(
        scripts_dir / spec["scanner"],
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
    # T-283: record the leap wrapper's retry waits instead of sleeping them —
    # `assert 1 >= 2` here was one of the 2026-08-29 gate's load reds.
    _executable(
        bin_dir / "sleep-recorder",
        textwrap.dedent(
            """\
            #!/bin/bash
            exit 0
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
    # The ladder reads the clock twice per attempt. A scripted 1s-per-attempt
    # epoch is the tightest reading an instant 502 can produce, so the exact
    # POST count below stops being a sample of where the second boundary fell
    # (CI 2026-09-04, shards scripts-jm/scripts-gh).
    counter = tmp_path / "clock.n"
    _executable(
        bin_dir / "clock",
        textwrap.dedent(
            f"""\
            #!/bin/bash
            n=$(cat {str(counter)!r} 2>/dev/null || echo 0)
            n=$((n + 1))
            echo "$n" > {str(counter)!r}
            echo $((1700000000 + n))
            """
        ),
    )
    return repo, py


def _run(
    repo: Path,
    py: Path,
    spec: dict,
    port: int,
    timeout_secs: str | None = None,
    extra_env: dict | None = None,
):
    env = {
        **os.environ,
        "RADON_PYTHON_BIN": str(py),
        spec["port_env"]: str(port),
        "RADON_LEAP_SLEEP_CMD": str(repo.parent / "bin" / "sleep-recorder"),
        "RADON_GARCH_SLEEP_CMD": str(repo.parent / "bin" / "sleep-recorder"),
        "RADON_LEAP_NOW_CMD": str(repo.parent / "bin" / "clock"),
        "RADON_GARCH_NOW_CMD": str(repo.parent / "bin" / "clock"),
    }
    if timeout_secs is not None:
        env["RADON_SCAN_FASTAPI_TIMEOUT_SECS"] = timeout_secs
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(repo / "scripts" / spec["wrapper"])],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )


@pytest.mark.parametrize("name", sorted(WRAPPERS))
def test_capacity_502_does_not_launch_a_direct_duplicate(tmp_path, name):
    spec = WRAPPERS[name]
    marker = tmp_path / "direct-ran"
    repo, py = _repo(tmp_path, spec, marker)
    port = _free_port()
    # Leap (2026-08-27) and garch (2026-09-01) wait out capacity shed;
    # keep the wait tiny here so the no-duplicate assertion stays fast.
    extra = {
        "RADON_LEAP_SHED_WAIT_SECS": "2",
        "RADON_LEAP_REFRESH_RETRY_DELAY_SECS": "1",
        "RADON_GARCH_SHED_WAIT_SECS": "2",
        "RADON_GARCH_REFRESH_RETRY_DELAY_SECS": "1",
    }

    with _Stub(port, 502) as stub:
        result = _run(repo, py, spec, port, extra_env=extra)

    assert not marker.exists(), (
        f"{spec['scanner']} ran directly after a 502 — outside "
        "MAX_CONCURRENT_SUBPROCESSES and outside the FastAPI cooldown"
    )
    assert result.returncode != 0, result.stdout + result.stderr
    combined = (result.stdout + result.stderr).lower()
    # 2s budget at a 1s delay is three POSTs when the POSTs themselves are
    # free, but the budget is wall clock: under CI load a slow POST eats a
    # retry slot and only two fit. Pin what the wrapper guarantees — it
    # re-POSTs the SAME endpoint and never anything else, and it gives up
    # inside the budget rather than looping — not a count the clock owns.
    assert set(stub.calls) == {spec["path"]}, stub.calls
    assert 2 <= len(stub.calls) <= 3, stub.calls
    assert "capacity" in combined or "shed" in combined


@pytest.mark.parametrize("name", sorted(WRAPPERS))
def test_curl_timeout_does_not_launch_a_direct_duplicate(tmp_path, name):
    spec = WRAPPERS[name]
    marker = tmp_path / "direct-ran"
    repo, py = _repo(tmp_path, spec, marker)
    port = _free_port()

    with _Stub(port, None) as stub:
        result = _run(repo, py, spec, port, timeout_secs="2")

    assert stub.calls == [spec["path"]], stub.calls
    assert not marker.exists(), (
        f"{spec['scanner']} ran directly while FastAPI's child may still be "
        "mid-flight"
    )
    assert result.returncode != 0


@pytest.mark.parametrize("name", sorted(WRAPPERS))
def test_connection_refused_still_falls_back(tmp_path, name):
    """Control: nothing was accepted, so a direct run is safe and required."""
    spec = WRAPPERS[name]
    marker = tmp_path / "direct-ran"
    repo, py = _repo(tmp_path, spec, marker)

    result = _run(repo, py, spec, _free_port())  # nothing listening

    assert marker.exists(), result.stdout + result.stderr
    assert result.returncode == 0, result.stdout + result.stderr
