"""POST /rotate-password on the broker daemon.

The operator changes the IBKR password on IBKR's site, then saves it in the
web app. The app host forwards it here over mTLS. The daemon validates it,
refuses while a Gateway login would stack a 2FA push (cooldown, lease, a
rotation already running), writes it through the root writer with
`sudo -n <writer> write` (password on stdin, never argv), then recreates the
Gateway once through the helper's own `restart`. During an operator hold or an
IBKR login throttle it writes and defers the restart: the old password is
already dead, so leaving it in place guarantees the next sanctioned login
fails.

The sudo stub drops `-n` and execs the writer stub, which logs its argv and
stdin. The helper stub logs each verb.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ib_gateway_remote import serve  # noqa: E402
from test_ib_gateway_remote import (  # noqa: E402
    _call,
    _config,
    _ctx,
    _helper_script,
    _start,
    _stop,
    mint_mtls,
)
from test_ib_gateway_remote_serve import _lease_file  # noqa: E402
from utils import ib_operator_hold  # noqa: E402

PASSWORD = "N3w$pa ss#9"
THROTTLE_LINE = (
    "2026-09-26 22:40:32:372 IBC: Too many failed login attempts. "
    "Please wait 22 seconds before attempting to re-login again."
)


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_PATH", str(tmp_path / "hold.json"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_AUDIT", str(tmp_path / "hold.jsonl"))
    monkeypatch.setenv("RADON_IB_OPERATOR_HOLD_OWNER_UID", str(os.getuid()))
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "absent" / "lock.json"))
    monkeypatch.delenv("STATE_DIRECTORY", raising=False)
    monkeypatch.delenv("RADON_IB_REMOTE_STATE_DIR", raising=False)
    with serve._verb_history_guard:
        serve._verb_history.clear()
        serve._verb_wall_history.clear()
    yield
    with serve._verb_history_guard:
        serve._verb_history.clear()
        serve._verb_wall_history.clear()


@pytest.fixture
def certs(tmp_path):
    pki = tmp_path / "pki"
    pki.mkdir()
    return mint_mtls(pki)


def _sudo_stub(tmp_path: Path) -> Path:
    path = tmp_path / "sudo"
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$@\" > '{tmp_path / 'sudo.argv'}'\n"
        '[ "$1" = "-n" ] || exit 99\n'
        "shift\n"
        'exec "$@"\n'
    )
    path.chmod(0o755)
    return path


def _writer_stub(tmp_path: Path, *, rc: int = 0, echo_stdin: bool = False, sleep: float = 0) -> Path:
    path = tmp_path / "radon-ib-gateway-credential"
    body = (
        "#!/bin/sh\n"
        f"cat > '{tmp_path / 'writer.stdin'}'\n"
        f"printf '%s\\n' \"$@\" > '{tmp_path / 'writer.argv'}'\n"
    )
    if sleep:
        body += f"sleep {sleep}\n"
    if echo_stdin:
        body += f"echo \"refused: $(cat '{tmp_path / 'writer.stdin'}')\" >&2\n"
    else:
        body += "echo 'updated: TWS_PASSWORD'\n"
    body += f"exit {rc}\n"
    path.write_text(body)
    path.chmod(0o755)
    return path


def _daemon(tmp_path, certs, *, helper=None, writer=None, **overrides):
    config = _config(
        tmp_path,
        certs,
        helper=helper or _helper_script(tmp_path, stdout="ok"),
        RADON_IB_CREDENTIAL_WRITER=writer or _writer_stub(tmp_path),
        RADON_IB_REMOTE_SUDO=_sudo_stub(tmp_path),
        RADON_IB_WATCHDOG_STATE_PATH=tmp_path / "ib-watchdog-state.json",
        **overrides,
    )
    return _start(config)


@pytest.fixture
def httpd(tmp_path, certs):
    server = _daemon(tmp_path, certs)
    yield server
    _stop(server)


def _post(httpd, certs, path: str, payload) -> tuple[int, dict]:
    port = httpd.server_address[1]
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://127.0.0.1:{port}{path}",
        method="POST",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, context=_ctx(certs), timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def _rotate(httpd, certs, **body) -> tuple[int, dict]:
    payload = {"password": PASSWORD, "actor": "user_op", **body}
    return _post(httpd, certs, "/rotate-password", payload)


def _helper_calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "helper.log"
    return log.read_text().split() if log.exists() else []


def _writer_called(tmp_path: Path) -> bool:
    return (tmp_path / "writer.argv").exists()


def _verb(httpd, certs, verb: str) -> tuple[int, dict]:
    try:
        return _call(httpd, certs, f"/{verb}", "POST")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


def test_happy_path(httpd, certs, tmp_path, capfd):
    status, body = _rotate(httpd, certs)

    assert status == 200, body
    assert (body["ok"], body["written"], body["restarted"]) == (True, True, True)
    sudo_argv = (tmp_path / "sudo.argv").read_text().splitlines()
    assert sudo_argv == ["-n", str(tmp_path / "radon-ib-gateway-credential"), "write"]
    assert (tmp_path / "writer.argv").read_text().splitlines() == ["write"]
    assert json.loads((tmp_path / "writer.stdin").read_text()) == {"TWS_PASSWORD": PASSWORD}
    assert _helper_calls(tmp_path) == ["restart"]
    captured = capfd.readouterr()
    assert PASSWORD not in json.dumps(body)
    assert PASSWORD not in (tmp_path / "helper.log").read_text()
    assert PASSWORD not in captured.err + captured.out
    assert PASSWORD not in "".join((tmp_path / "sudo.argv").read_text())


def test_userid_forwarded(httpd, certs, tmp_path):
    status, _ = _rotate(httpd, certs, userid="newuser9")
    assert status == 200
    assert json.loads((tmp_path / "writer.stdin").read_text()) == {
        "TWS_PASSWORD": PASSWORD,
        "TWS_USERID": "newuser9",
    }


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(b"not json", id="not-json"),
        pytest.param(b"[1, 2]", id="not-object"),
        pytest.param({"actor": "u"}, id="password-missing"),
        pytest.param({"password": ""}, id="empty"),
        pytest.param({"password": "it's"}, id="single-quote"),
        pytest.param({"password": "a\nb"}, id="newline"),
        pytest.param({"password": "a\x00b"}, id="nul"),
        pytest.param({"password": "x\u2028JAVA_TOOL_OPTIONS=-javaagent:/tmp/a.jar #"}, id="u2028"),
        pytest.param({"password": "x\u2029y"}, id="u2029"),
        pytest.param({"password": "x\x85y"}, id="nel-u0085"),
        pytest.param({"password": "abc\\"}, id="trailing-backslash"),
        pytest.param({"password": " abc"}, id="leading-space"),
        pytest.param({"password": "x" * 257}, id="too-long"),
        pytest.param({"password": 123}, id="not-a-string"),
        pytest.param({"password": "fine", "userid": "bad user\n"}, id="bad-userid"),
        pytest.param({"password": "fine", "extra": "x"}, id="unknown-key"),
    ],
)
def test_invalid_body(httpd, certs, tmp_path, payload):
    status, body = _post(httpd, certs, "/rotate-password", payload)
    assert status == 400, body
    assert body["code"] == "INVALID_VALUE"
    assert not _writer_called(tmp_path)
    assert _helper_calls(tmp_path) == []
    if isinstance(payload, dict) and isinstance(payload.get("password"), str) and payload["password"].strip():
        assert payload["password"].strip() not in json.dumps(body)


def test_oversize_413(httpd, certs, tmp_path):
    """Declared length alone triggers 413, so no body races the daemon's close.

    Sending a real 5000-byte body made the client hit ConnectionResetError at
    random: the daemon answers and closes before reading it.
    """
    import http.client

    port = httpd.server_address[1]
    conn = http.client.HTTPSConnection("127.0.0.1", port, context=_ctx(certs), timeout=10)
    try:
        conn.putrequest("POST", "/rotate-password")
        conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", "5000")
        conn.endheaders()
        resp = conn.getresponse()
        status = resp.status
        resp.read()
    finally:
        conn.close()
    assert status == 413
    assert not _writer_called(tmp_path)
    assert _helper_calls(tmp_path) == []


def test_cooldown_after_stop_409_no_write(httpd, certs, tmp_path):
    assert _verb(httpd, certs, "stop")[0] == 200
    status, body = _rotate(httpd, certs)
    assert status == 409, body
    assert (body["code"], body["reason"]) == ("LOGIN_BLOCKED", "cooldown")
    assert not _writer_called(tmp_path)
    assert _helper_calls(tmp_path) == ["stop"]


def test_lease_held_409_no_write(tmp_path, certs, monkeypatch):
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(_lease_file(tmp_path)))
    server = _daemon(tmp_path, certs)
    try:
        status, body = _rotate(server, certs)
    finally:
        _stop(server)
    assert status == 409, body
    assert (body["code"], body["reason"]) == ("LOGIN_BLOCKED", "lease")
    assert "radon-cloud.ib-watchdog" in body["detail"]
    assert not _writer_called(tmp_path)
    assert _helper_calls(tmp_path) == []


def test_second_rotate_within_60s_409(httpd, certs, tmp_path):
    assert _rotate(httpd, certs)[0] == 200
    (tmp_path / "writer.argv").unlink()
    status, body = _rotate(httpd, certs)
    assert status == 409, body
    assert body["reason"] == "cooldown"
    assert not _writer_called(tmp_path)
    assert _helper_calls(tmp_path) == ["restart"]


def test_restart_within_60s_of_rotate_409(httpd, certs, tmp_path):
    assert _rotate(httpd, certs)[0] == 200
    status, _ = _verb(httpd, certs, "restart")
    assert status == 409
    status, _ = _verb(httpd, certs, "start")
    assert status == 409
    assert _helper_calls(tmp_path) == ["restart"]


def test_rotation_in_progress_409(httpd, certs, tmp_path):
    assert serve._rotate_guard.acquire(blocking=False)
    try:
        status, body = _rotate(httpd, certs)
    finally:
        serve._rotate_guard.release()
    assert status == 409, body
    assert body["code"] == "ROTATION_IN_PROGRESS"
    assert not _writer_called(tmp_path)


def test_hold_writes_then_defers_202_no_helper(httpd, certs, tmp_path):
    ib_operator_hold.set_hold("operator web login", "test")
    status, body = _rotate(httpd, certs)
    assert status == 202, body
    assert (body["written"], body["restarted"], body["code"]) == (True, False, "RESTART_DEFERRED")
    assert "hold" in body["detail"].lower()
    assert _writer_called(tmp_path)
    assert _helper_calls(tmp_path) == []


def test_throttle_writes_then_defers_202_no_helper(httpd, certs, tmp_path):
    (tmp_path / "ib-watchdog-state.json").write_text(json.dumps({
        "login_throttle_since": time.time() - 60,
        "login_throttle_line": THROTTLE_LINE,
        "login_throttle_retries": 0,
    }))
    status, body = _rotate(httpd, certs)
    assert status == 202, body
    assert (body["written"], body["restarted"], body["code"]) == (True, False, "RESTART_DEFERRED")
    assert "too many failed login" in body["detail"].lower()
    assert _writer_called(tmp_path)
    assert _helper_calls(tmp_path) == []


def test_writer_fail_502_not_written_redacted(tmp_path, certs, capfd):
    server = _daemon(tmp_path, certs, writer=_writer_stub(tmp_path, rc=65, echo_stdin=True))
    try:
        status, body = _rotate(server, certs)
    finally:
        _stop(server)
    assert status == 502, body
    assert (body["written"], body["code"]) == (False, "WRITE_FAILED")
    assert PASSWORD not in json.dumps(body)
    assert "[redacted]" in body["detail"]
    assert _helper_calls(tmp_path) == []
    captured = capfd.readouterr()
    assert PASSWORD not in captured.err + captured.out


def test_writer_timeout_502_unknown(tmp_path, certs, monkeypatch):
    monkeypatch.setattr(serve, "WRITER_TIMEOUT_S", 0.5)
    server = _daemon(tmp_path, certs, writer=_writer_stub(tmp_path, sleep=3))
    try:
        status, body = _rotate(server, certs)
    finally:
        _stop(server)
    assert status == 502, body
    assert body["written"] is None
    assert body["code"] == "WRITE_UNKNOWN"
    assert _helper_calls(tmp_path) == []


def test_helper_75_is_202_deferred(tmp_path, certs):
    helper = _helper_script(tmp_path, rc=serve.LEASE_HELD_RC, stdout="lease held by watchdog")
    server = _daemon(tmp_path, certs, helper=helper)
    try:
        status, body = _rotate(server, certs)
    finally:
        _stop(server)
    assert status == 202, body
    assert (body["written"], body["restarted"], body["code"]) == (True, False, "RESTART_DEFERRED")
    assert _helper_calls(tmp_path) == ["restart"]


def test_helper_1_is_502_restart_failed_written(tmp_path, certs):
    helper = _helper_script(tmp_path, rc=1, stdout="compose up failed")
    server = _daemon(tmp_path, certs, helper=helper)
    try:
        status, body = _rotate(server, certs)
    finally:
        _stop(server)
    assert status == 502, body
    assert (body["ok"], body["written"], body["restarted"], body["code"]) == (
        False, True, False, "RESTART_FAILED"
    )
    assert body["returncode"] == 1
    assert _helper_calls(tmp_path) == ["restart"], "a failed restart must not be retried"


def test_rotate_not_a_helper_verb(tmp_path):
    helper = _helper_script(tmp_path)
    rc, _detail = serve.run_helper(str(helper), "rotate-password", 5)
    assert rc == 2
    assert _helper_calls(tmp_path) == []
    assert "rotate-password" not in serve.VERBS
    assert "rotate-password" not in serve.MUTATIONS


def test_value_rule_vectors_match_the_app_copy():
    """Three copies of one rule: the app route, this daemon, the root writer."""
    import importlib.util

    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "writer", repo / "cloud" / "scripts" / "radon-ib-gateway-credential.py"
    )
    writer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(writer)
    from api.routes import credentials

    vectors = ["ok", "p$ab c#d", 'x"y', "it's", "a\nb", "a\rb", "a\x00b", "a\x7fb",
               "abc\\", " a", "a ", "", "x" * 256, "x" * 257, "a\\b", "été",
               "lone\ud800surrogate", " nbsp-edge", "a\u2028b", "a\u2029b", "a\x85b",
               "a\x1cb", "a\x1db", "a\x1eb", "a\x0bb", "a\x0cb", "a\u00a0b"]
    for value in vectors:
        daemon = serve._gateway_value_error(value) is None
        app = credentials._gateway_value_error(value) is None
        root = writer.value_error(value) is None
        assert daemon == app == root, repr(value)
    # Anything str.splitlines() treats as a line break could split the stored
    # line on a later rewrite and inject a KEY=value line into the env_file.
    for value in vectors:
        if len((value + "x").splitlines()) > 1:
            assert writer.value_error(value) is not None, repr(value)
    for ch in ("\u2028", "\u2029", "\x85"):
        assert serve._gateway_value_error(f"a{ch}b") is not None
        assert credentials._gateway_value_error(f"a{ch}b") is not None
        assert writer.value_error(f"a{ch}b") is not None
