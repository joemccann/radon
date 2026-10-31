"""Preparatory model containers cannot replace a systemd service's main PID."""
from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

import pytest

CLOUD = Path(__file__).resolve().parents[1]


@pytest.fixture
def notify_socket():
    # Darwin Unix socket paths have a shorter limit than pytest's temp roots.
    with tempfile.TemporaryDirectory(prefix="model-notify-", dir="/tmp") as folder:
        path = str(Path(folder) / "notify.sock")
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as listener:
            listener.bind(path)
            listener.settimeout(0.05)
            yield path, listener


@pytest.fixture
def notifying_engine(tmp_path):
    engine = tmp_path / "podman"
    engine.write_text(
        f"#!{sys.executable}\n"
        "import os, socket, sys\n"
        "if len(sys.argv) > 1 and sys.argv[1] == 'run':\n"
        "    path = os.environ.get('NOTIFY_SOCKET')\n"
        "    if path and '--sdnotify=ignore' not in sys.argv:\n"
        "        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sender:\n"
        "            sender.sendto(b'MAINPID=424242\\n', path)\n"
        "    sys.stdout.buffer.write(b'fixture artifact')\n"
    )
    engine.chmod(0o755)
    return engine


def assert_no_notification(listener):
    with pytest.raises(socket.timeout):
        listener.recv(1024)


def test_cache_migration_cannot_notify_systemd_or_clear_foreground_environment(
    tmp_path, notifying_engine, notify_socket
):
    path, listener = notify_socket
    runtime = (CLOUD / "scripts/radon-app-runtime.sh").read_text()
    function = "migrate_model_cache() {" + runtime.split(
        "migrate_model_cache() {", 1
    )[1].split("\n}", 1)[0] + "\n}\n"
    cache = tmp_path / "cache"
    (cache / "pacing-imports").mkdir(parents=True)
    result = subprocess.run(
        ["/bin/bash", "-eu", "-c", function + """
require_bind_source() { :; }
migrate_model_cache 1000:1000 fixture-image "$CACHE"
printf '\nforeground-notify=%s\n' "$NOTIFY_SOCKET"
"""],
        env={**os.environ, "DOCKER": str(notifying_engine), "ENGINE": "podman",
             "CACHE": str(cache), "RADON_SUBSCRIPTION_HOME": str(tmp_path / "home"),
             "NOTIFY_SOCKET": path},
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert f"foreground-notify={path}" in result.stdout
    assert_no_notification(listener)


@pytest.mark.parametrize("darwin", [False, True], ids=["linux", "darwin"])
def test_client_export_cannot_notify_systemd_or_clear_foreground_environment(
    notifying_engine, notify_socket, monkeypatch, darwin
):
    path, listener = notify_socket
    spec = importlib.util.spec_from_file_location(
        "model_clients_notifications", CLOUD / "scripts/model_clients.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("NOTIFY_SOCKET", path)
    target = io.BytesIO()
    module.export_from_image(str(notifying_engine), "fixture-image", target, darwin=darwin)
    assert target.read() == b"fixture artifact"
    assert os.environ["NOTIFY_SOCKET"] == path
    assert_no_notification(listener)
