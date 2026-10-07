"""DS-2026-10-05-05: the subscription-token vault broker.

radon-subscription-tokens runs third-party CLIs (agy, grok, codex) out of
radon-writable directories, so it must never hold the secret-store master key.
The broker is the only process that opens the store for it; it runs inside
radon-app-runtime and serves exactly the four CLI credential slots over a unix
socket. The CLI daemon talks to it through BrokerVault.
"""

from __future__ import annotations

import builtins
import json
import logging
import os
import socket
import tempfile
import threading
from pathlib import Path

import pytest

from scripts import subscription_tokens as st
from scripts import subscription_vault as sv


class Store:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    def get_secret(self, name):
        return self.rows.get(name)

    def set_secret(self, name, value, _service):
        self.rows[name] = value


@pytest.fixture
def broker():
    # macOS AF_UNIX paths are capped at 104 bytes; pytest tmp_path overflows it.
    root = Path(tempfile.mkdtemp(prefix="rsv", dir="/tmp"))
    path = root / "vault.sock"
    store = Store({"SUBSCRIPTION_TOKEN_ANTHROPIC": "sealed-anthropic", "UW_TOKEN": "not-for-the-daemon"})
    stop = threading.Event()
    ready = threading.Event()
    thread = threading.Thread(
        target=sv.serve, args=(str(path), st.Vault(store)), kwargs={"stop": stop, "ready": ready}, daemon=True
    )
    thread.start()
    assert ready.wait(5)
    yield path, store
    stop.set()
    thread.join(5)


def _raw(path: Path, payload: bytes) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
        conn.settimeout(5)
        conn.connect(str(path))
        conn.sendall(payload)
        conn.shutdown(socket.SHUT_WR)
        data = b""
        while chunk := conn.recv(65536):
            data += chunk
    return json.loads(data)


def test_round_trip_get_and_seal_through_the_socket(broker):
    path, store = broker
    vault = sv.connect_vault(str(path))
    assert vault.get("anthropic") == "sealed-anthropic"
    assert vault.get("codex") is None
    vault.seal("codex", '{"tokens": {}}')
    assert store.rows["SUBSCRIPTION_TOKEN_CODEX"] == '{"tokens": {}}'
    assert vault.get("codex") == '{"tokens": {}}'


def test_the_broker_serves_only_the_cli_credential_slots(broker):
    path, store = broker
    for provider in ("UW_TOKEN", "SUBSCRIPTION_TOKEN_ANTHROPIC", "../anthropic", "claude", ""):
        reply = _raw(path, json.dumps({"op": "get", "provider": provider}).encode())
        assert reply == {"ok": False, "error": "unknown provider"}
    reply = _raw(path, json.dumps({"op": "seal", "provider": "UW_TOKEN", "value": "x"}).encode())
    assert reply == {"ok": False, "error": "unknown provider"}
    assert store.rows["UW_TOKEN"] == "not-for-the-daemon"


def test_the_broker_refuses_unknown_ops_and_malformed_requests(broker):
    path, _store = broker
    assert _raw(path, b'{"op": "dump"}') == {"ok": False, "error": "unknown op"}
    assert _raw(path, b"not json") == {"ok": False, "error": "malformed request"}
    assert _raw(path, b'["get"]') == {"ok": False, "error": "malformed request"}
    reply = _raw(path, json.dumps({"op": "seal", "provider": "anthropic", "value": 7}).encode())
    assert reply == {"ok": False, "error": "malformed request"}


def test_the_broker_refuses_an_oversized_request(broker):
    path, store = broker
    huge = json.dumps({"op": "seal", "provider": "anthropic", "value": "x" * (sv.MAX_REQUEST_BYTES + 1)})
    assert _raw(path, huge.encode()) == {"ok": False, "error": "request too large"}
    assert store.rows["SUBSCRIPTION_TOKEN_ANTHROPIC"] == "sealed-anthropic"


def test_the_socket_is_owner_only(broker):
    path, _store = broker
    assert (os.stat(path).st_mode & 0o777) == 0o600


def test_no_token_value_is_logged(broker, caplog):
    path, _store = broker
    caplog.set_level(logging.DEBUG)
    vault = sv.connect_vault(str(path))
    vault.seal("grok", "SECRET-REFRESH-VALUE")
    vault.get("grok")
    assert "SECRET-REFRESH-VALUE" not in caplog.text
    assert "sealed-anthropic" not in caplog.text


def test_a_store_failure_is_reported_without_detail(broker, monkeypatch):
    path, store = broker

    def boom(*_args):
        raise OSError("disk full at /secret/path")

    monkeypatch.setattr(store, "set_secret", boom)
    reply = _raw(path, json.dumps({"op": "seal", "provider": "anthropic", "value": "v"}).encode())
    assert reply == {"ok": False, "error": "store error: OSError"}
    vault = sv.connect_vault(str(path))
    with pytest.raises(st.VaultUnavailable):
        vault.seal("anthropic", "v")


def test_connect_fails_closed_as_vault_unavailable_when_no_broker(tmp_path):
    with pytest.raises(st.VaultUnavailable):
        sv.connect_vault(str(tmp_path / "absent.sock"), wait_seconds=0.2)


def test_the_daemon_opens_the_broker_and_never_the_store(broker, monkeypatch):
    path, _store = broker
    real_import = builtins.__import__

    def refuse_store(name, *args, **kwargs):
        if name.endswith("secret_store"):
            raise AssertionError("the CLI daemon imported the secret store")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse_store)
    vault = st.open_vault({sv.SOCKET_ENV: str(path)})
    assert vault.get("anthropic") == "sealed-anthropic"


def test_the_daemon_reports_store_unavailable_when_the_broker_is_down(tmp_path, monkeypatch):
    monkeypatch.setattr(sv, "CONNECT_WAIT_SECONDS", 0.2)
    with pytest.raises(st.VaultUnavailable):
        st.open_vault({sv.SOCKET_ENV: str(tmp_path / "absent.sock")})


def test_serve_replaces_a_stale_socket_but_never_a_regular_file(tmp_path):
    target = tmp_path / "vault.sock"
    target.write_text("not a socket")
    with pytest.raises(SystemExit):
        sv.serve(str(target), st.Vault(Store()), stop=threading.Event(), ready=threading.Event())
    assert target.read_text() == "not a socket"
