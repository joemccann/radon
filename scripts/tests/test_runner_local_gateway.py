#!/usr/bin/env python3
"""Runner model calls use a Mac-local gateway and the Mac's own subscriptions.

The runner installer copies a fixed gateway source list into a root-owned
tree and supervises it as a dedicated account. Runner sessions reach it at a
local socket; nothing in the runner path forwards to the VPS.
"""
from __future__ import annotations

import http.client
import json
import os
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "scripts" / "runner"
INSTALL = RUNNER / "install.sh"
SOCKET = "/Library/Radon/model-gateway-local/run/gateway.sock"
CONTROL = "/Library/Radon/model-gateway-local/control/control.sock"
HOME = "/Library/Radon/model-gateway-local/home"


def gateway_files() -> list[str]:
    block = re.search(r'GATEWAY_FILES="([^"]+)"', INSTALL.read_text(encoding="utf-8"))
    assert block, "install.sh must declare GATEWAY_FILES"
    return block.group(1).split()


def printed(flag: str) -> dict:
    result = subprocess.run(
        ["bash", str(INSTALL), flag],
        env={**os.environ, "RADON_GATEWAY_CLIENT_UID": "502", "RADON_GATEWAY_GID": "401"},
        capture_output=True, text=True, timeout=30, check=True,
    )
    return plistlib.loads(result.stdout.encode())


def test_runner_sessions_default_to_the_local_gateway_socket():
    text = (RUNNER / "run_loop.sh").read_text(encoding="utf-8")
    assert f'RADON_MODEL_GATEWAY_SOCKET="${{RADON_MODEL_GATEWAY_SOCKET:-{SOCKET}}}"' in text
    assert "/Library/Radon/model-gateway/gateway.sock" not in text


def test_gateway_daemon_runs_the_root_installed_copy_as_its_own_account():
    plist = printed("--print-gateway-plist")
    assert plist["Label"] == "com.radon.model-gateway-local"
    assert plist["UserName"] == "_radonmodelgw" and plist["GroupName"] == "_radonmodel"
    assert plist["KeepAlive"] is True and plist["RunAtLoad"] is True
    assert plist["ProgramArguments"] == [
        "/usr/local/radon-runner/gateway/venv/bin/python", "-I",
        "/usr/local/radon-runner/gateway/gateway_launch.py", "serve",
        "--socket", SOCKET, "--control-socket", CONTROL,
    ]
    env = plist["EnvironmentVariables"]
    assert env["HOME"] == HOME
    assert env["CLAUDE_CODE_OAUTH_TOKEN_FILE"] == HOME + "/claude-oauth-token"
    assert env["CODEX_HOME"] == HOME + "/.codex"
    assert env["RADON_SUBSCRIPTION_STATE_DIR"] == HOME + "/state"
    assert env["RADON_MODEL_CLIENT_UIDS"] == "502"
    assert env["RADON_MODEL_TUNNEL_GID"] == "401"
    assert env["RADON_MODEL_PROVIDER_CONCURRENCY"] == "6"
    assert env["PATH"].split(":")[0] == "/Library/Radon/model-clients/current/bin"
    # No provider credential value is ever written into a plist.
    assert not {"CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"} & set(env)


def test_no_periodic_renewal_job_is_installed():
    """Renewal is on demand, one provider, non-interactive (engine refresh on
    an expired grant). A timer would run the interactive all-provider pass and
    page for providers this Mac never configured."""
    assert "renew" not in INSTALL.read_text(encoding="utf-8")


def test_gateway_requirements_are_hash_pinned():
    text = (RUNNER / "gateway-requirements.txt").read_text(encoding="utf-8")
    pins = re.findall(r"(?m)^([A-Za-z0-9_.-]+)==", text)
    assert "httpx" in pins
    for block in re.split(r"(?m)^(?=[A-Za-z0-9_.-]+==)", text)[1:]:
        assert "--hash=sha256:" in block, block.splitlines()[0]


def _get(path: str, url: str) -> tuple[int, dict]:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5)
    sock.connect(path)
    conn = http.client.HTTPConnection("localhost", timeout=5)
    conn.sock = sock
    conn.request("GET", url)
    response = conn.getresponse()
    return response.status, json.loads(response.read())


def _post(path: str, url: str, payload: dict) -> tuple[int, dict]:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10)
    sock.connect(path)
    conn = http.client.HTTPConnection("localhost", timeout=10)
    conn.sock = sock
    conn.request("POST", url, body=json.dumps(payload), headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    return response.status, json.loads(response.read())


def test_installed_file_list_is_enough_to_serve(tmp_path):
    """Stage the gateway with install.sh's own copy code, then start it.

    A module missing from the list, or a copy step that mangles a path (the
    first live install turned top-level `subscription_tokens.py` into a
    directory), fails here, not at 00:50 on the Mac.
    """
    root = tmp_path / "gateway"
    subprocess.run(["bash", str(INSTALL), "--stage-gateway", str(root)],
                   env={**os.environ, "RADON_RUNNER_PYTHON": sys.executable},
                   capture_output=True, text=True, timeout=60, check=True)
    for name in gateway_files():
        staged = root / "src" / name
        assert staged.is_file() and staged.read_bytes() == (REPO / "scripts" / name).read_bytes(), name
    assert (root / "gateway_launch.py").read_bytes() == (RUNNER / "gateway_launch.py").read_bytes()
    # AF_UNIX paths are capped near 104 bytes on macOS.
    run = Path(tempfile.mkdtemp(prefix="rg", dir="/tmp"))
    try:
        sock = run / "s" / "g.sock"
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "LANG": "C"}
        proc = subprocess.Popen(
            [sys.executable, "-I", str(root / "gateway_launch.py"), "serve",
             "--socket", str(sock), "--control-socket", str(run / "c" / "c.sock")],
            env=env, cwd=str(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 20
            while not sock.exists():
                assert proc.poll() is None, proc.stdout.read().decode()
                assert time.monotonic() < deadline, "gateway did not bind its socket"
                time.sleep(0.05)
            status, body = _get(str(sock), "/health")
            assert status == 200 and body["status"] == "ok"
            status, body = _get(str(sock), "/v1/models")
            assert status == 200 and isinstance(body["models"], list)
            # No grant in this HOME: the native path loads and fails closed.
            status, body = _post(str(sock), "/v1/native", {
                "protocol": "messages", "stream": False,
                "request": {"model": "claude-sonnet-5", "max_tokens": 5,
                            "messages": [{"role": "user", "content": "hi"}]}})
            assert status == 503 and body["error"]["attempted"] == ["anthropic:no_key."]
        finally:
            proc.terminate()
            proc.wait(10)
    finally:
        shutil.rmtree(run, ignore_errors=True)


@pytest.mark.parametrize("argv", [[], ["status"], ["renew"]])
def test_launcher_rejects_unknown_commands(argv):
    result = subprocess.run([sys.executable, "-I", str(RUNNER / "gateway_launch.py"), *argv],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 64
