"""Relay WatchdogSec contract: a wedged child that stops pinging is dead.

Host units stay Type=notify. A container ExecStart must forward NOTIFY_SOCKET
or systemd never sees WATCHDOG=1 and cannot restart a wedged event loop.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

CLOUD = Path(__file__).resolve().parents[1]
REPO = CLOUD.parent
RELAY_UNIT = CLOUD / "services" / "radon-relay.service"
DROPIN = (
    CLOUD / "services" / "radon-relay.service.d" / "runtime-container.conf.example"
)
RUNTIME = CLOUD / "scripts" / "radon-app-runtime.sh"
RELAY_JS = REPO / "scripts" / "ib_realtime_server.js"

WATCHDOG_SEC = 0.25
CHILD = r"""
import os, socket, sys, time
addr = os.environ["NOTIFY_SOCKET"]
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.connect(addr)
s.send(b"READY=1\n")
if sys.argv[1] == "wedge":
    time.sleep(30)
    raise SystemExit(0)
while True:
    s.send(b"WATCHDOG=1\n")
    time.sleep(0.05)
"""


def _collect_notify(sock: socket.socket, deadline: float) -> list[str]:
    got: list[str] = []
    while time.monotonic() < deadline:
        sock.settimeout(max(0.01, deadline - time.monotonic()))
        try:
            data, _ = sock.recvfrom(256)
        except TimeoutError:
            continue
        got.extend(part for part in data.decode().splitlines() if part)
    return got


def _short_notify_sock() -> tuple[Path, socket.socket]:
    # macOS AF_UNIX sockaddr is 104 bytes; pytest tmp_path overflows it.
    d = Path(tempfile.mkdtemp(prefix="rdn", dir="/tmp"))
    sock_path = d / "n"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    server.bind(str(sock_path))
    return sock_path, server


def test_wedged_child_misses_watchdog_inside_container_env() -> None:
    """READY=1 then silence is a missed WatchdogSec. Same env a container gets."""
    sock_path, server = _short_notify_sock()
    env = {**os.environ, "NOTIFY_SOCKET": str(sock_path), "WATCHDOG_USEC": "250000"}
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, "wedge"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        messages = _collect_notify(server, time.monotonic() + WATCHDOG_SEC * 2)
        assert "READY=1" in messages
        assert "WATCHDOG=1" not in messages
        missed = "WATCHDOG=1" not in messages
        assert missed, "wedged child must miss the watchdog"
    finally:
        proc.kill()
        proc.wait(timeout=5)
        server.close()


def test_pinging_child_satisfies_watchdog() -> None:
    sock_path, server = _short_notify_sock()
    env = {**os.environ, "NOTIFY_SOCKET": str(sock_path), "WATCHDOG_USEC": "250000"}
    proc = subprocess.Popen(
        [sys.executable, "-c", CHILD, "ping"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        messages = _collect_notify(server, time.monotonic() + WATCHDOG_SEC * 2)
        assert "READY=1" in messages
        assert "WATCHDOG=1" in messages
    finally:
        proc.kill()
        proc.wait(timeout=5)
        server.close()


def test_host_relay_unit_is_notify_watchdog() -> None:
    text = RELAY_UNIT.read_text(encoding="utf-8")
    assert "Type=notify" in text
    assert "WatchdogSec=45" in text
    assert "NotifyAccess=all" in text
    assert "docker run" not in text
    assert "ExecStart=/usr/bin/node scripts/ib_realtime_server.js" in text


def test_relay_source_pings_watchdog_when_socket_present() -> None:
    src = RELAY_JS.read_text(encoding="utf-8")
    assert "WATCHDOG=1" in src
    assert "NOTIFY_SOCKET" in src
    assert "sdNotify" in src


def test_container_dropin_forwards_notify_socket() -> None:
    dropin = DROPIN.read_text(encoding="utf-8")
    runtime = RUNTIME.read_text(encoding="utf-8")
    assert "NotifyAccess=all" in dropin
    assert "radon-app-runtime run %n" in dropin
    assert "docker.sock" not in dropin
    assert "docker.sock" not in runtime
    assert "NOTIFY_SOCKET" in runtime
    assert "WATCHDOG_USEC" in runtime
    assert "--network host" in runtime
    assert "--cgroupns" in runtime
