"""REL-168 fault injection for the broker's Gateway-control daemon.

R-471: one half-open TCP connection (no ClientHello) must not freeze the
listener -- the TLS handshake belongs on the per-connection worker thread,
under a socket timeout, and the IP allowlist is checked before it.
R-494: a post-handshake client that never finishes its request line is
released by the connection timeout; a huge ``Content-Length`` is refused
with 413 before any body is buffered and before the helper is spawned.
R-495: a CA-issued certificate is not enough -- the client's name must be
allowlisted, so the broker's own server pair cannot authorize a restart.
"""
from __future__ import annotations

import json
import socket
import ssl
import sys
import threading
import time
import urllib.error
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
    mint_mtls,
)


def _tls_socket(httpd, certs, *, cert=None, key=None) -> ssl.SSLSocket:
    """Handshaken mTLS socket to the daemon, raw so tests control the bytes."""
    ctx = _ctx(certs)
    if cert is not None:
        ctx = ssl.create_default_context(cafile=str(certs["ca"]))
        ctx.check_hostname = False
        ctx.load_cert_chain(str(cert), str(key))
    raw = socket.create_connection(("127.0.0.1", httpd.server_address[1]), timeout=5)
    return ctx.wrap_socket(raw, server_hostname="127.0.0.1")


def _read_response(sock: ssl.SSLSocket, timeout: float = 5.0) -> tuple[int, dict]:
    sock.settimeout(timeout)
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
    head, _, rest = buf.partition(b"\r\n\r\n")
    status = int(head.split(b" ", 2)[1])
    length = 0
    for line in head.split(b"\r\n")[1:]:
        name, _, value = line.partition(b":")
        if name.strip().lower() == b"content-length":
            length = int(value.strip())
    while len(rest) < length:
        chunk = sock.recv(4096)
        if not chunk:
            break
        rest += chunk
    return status, json.loads(rest.decode() or "{}")


class TestHalfOpenConnection:
    def test_no_clienthello_does_not_block_healthz(self, tmp_path):
        certs = mint_mtls(tmp_path)
        httpd = _start(_config(tmp_path, certs))
        port = httpd.server_address[1]
        idle = socket.create_connection(("127.0.0.1", port), timeout=5)
        try:
            # Give the accept loop time to pick the idle socket up.
            time.sleep(0.3)
            started = time.monotonic()
            status, body = _call(httpd, certs, "/healthz")
            elapsed = time.monotonic() - started
            assert status == 200 and body["ok"] is True
            assert elapsed < 2.0, f"/healthz blocked {elapsed:.1f}s behind a half-open socket"
        finally:
            idle.close()
            httpd.shutdown()

    def test_disallowed_peer_is_dropped_before_handshake(self, tmp_path):
        certs = mint_mtls(tmp_path)
        helper = _helper_script(tmp_path)
        config = _config(tmp_path, certs, helper=helper, RADON_IB_REMOTE_ALLOW="10.0.0.2")
        httpd = _start(config)
        try:
            with pytest.raises((ssl.SSLError, OSError, urllib.error.URLError)):
                _tls_socket(httpd, certs)
        finally:
            httpd.shutdown()
        assert not (tmp_path / "helper.log").exists()


class TestPostHandshakeBounds:
    def test_slow_loris_connections_are_released_by_the_timeout(self, tmp_path):
        certs = mint_mtls(tmp_path)
        config = _config(tmp_path, certs)
        config["conn_timeout"] = 1.0
        httpd = _start(config)
        socks = []
        try:
            for _ in range(5):
                sock = _tls_socket(httpd, certs)
                sock.sendall(b"GET / ")  # never a CRLF
                socks.append(sock)
            deadline = time.monotonic() + 4.0
            closed = 0
            for sock in socks:
                sock.settimeout(max(0.1, deadline - time.monotonic()))
                try:
                    if sock.recv(1) == b"":
                        closed += 1
                except (ssl.SSLEOFError, ConnectionResetError, ssl.SSLZeroReturnError):
                    closed += 1
                except (socket.timeout, TimeoutError):
                    pass
            assert closed == len(socks), f"{len(socks) - closed} slow-loris connections still pinned after the timeout"
            status, _ = _call(httpd, certs, "/healthz")
            assert status == 200
        finally:
            for sock in socks:
                sock.close()
            httpd.shutdown()

    def test_oversized_content_length_is_413_without_helper(self, tmp_path):
        certs = mint_mtls(tmp_path)
        helper = _helper_script(tmp_path)
        httpd = _start(_config(tmp_path, certs, helper=helper))
        sock = _tls_socket(httpd, certs)
        try:
            sock.sendall(
                b"POST /start HTTP/1.1\r\nHost: x\r\nContent-Length: 10000000\r\n\r\n"
            )
            status, body = _read_response(sock, timeout=5.0)
            assert status == 413, body
            assert body["ok"] is False
        finally:
            sock.close()
            httpd.shutdown()
        assert not (tmp_path / "helper.log").exists()

    def test_small_body_still_reaches_helper(self, tmp_path):
        certs = mint_mtls(tmp_path)
        helper = _helper_script(tmp_path)
        httpd = _start(_config(tmp_path, certs, helper=helper))
        sock = _tls_socket(httpd, certs)
        try:
            sock.sendall(b"POST /status HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
            status, _ = _read_response(sock)
            assert status == 404  # status is GET-only; body was consumed, no hang
            sock.close()
            sock = _tls_socket(httpd, certs)
            sock.sendall(b"POST /restart HTTP/1.1\r\nHost: x\r\nContent-Length: 2\r\n\r\n{}")
            status, body = _read_response(sock)
            assert status == 200 and body["ok"] is True
        finally:
            sock.close()
            httpd.shutdown()
        assert (tmp_path / "helper.log").read_text().splitlines() == ["restart"]


class TestClientIdentity:
    def test_server_pair_as_client_is_403(self, tmp_path):
        certs = mint_mtls(tmp_path)
        helper = _helper_script(tmp_path)
        httpd = _start(_config(tmp_path, certs, helper=helper))
        try:
            with pytest.raises(urllib.error.HTTPError) as err:
                ctx = ssl.create_default_context(cafile=str(certs["ca"]))
                ctx.check_hostname = False
                ctx.load_cert_chain(str(certs["cert"]), str(certs["key"]))
                _call(httpd, certs, "/restart", "POST", ctx=ctx)
            assert err.value.code == 403
            assert "certificate" in json.loads(err.value.read().decode())["error"]
        finally:
            httpd.shutdown()
        assert not (tmp_path / "helper.log").exists()

    def test_client_names_come_from_config(self, tmp_path):
        certs = mint_mtls(tmp_path)
        helper = _helper_script(tmp_path)
        config = _config(tmp_path, certs, helper=helper, RADON_IB_REMOTE_CLIENT_NAMES="radon-app")
        assert config["client_names"] == frozenset({"radon-app"})
        httpd = _start(config)
        try:
            with pytest.raises(urllib.error.HTTPError) as err:
                _call(httpd, certs, "/restart", "POST")  # fixture client is CN=client
            assert err.value.code == 403
        finally:
            httpd.shutdown()
        assert not (tmp_path / "helper.log").exists()

    def test_empty_client_names_is_a_config_error(self, tmp_path):
        certs = mint_mtls(tmp_path)
        with pytest.raises(serve.ConfigError):
            _config(tmp_path, certs, RADON_IB_REMOTE_CLIENT_NAMES=" , ")

    def test_client_cert_allowed_matches_cn_or_san(self):
        cert = {
            "subject": ((("commonName", "radon-app"),),),
            "subjectAltName": (("DNS", "app.radon.internal"),),
        }
        assert serve.client_cert_allowed(cert, {"radon-app"})
        assert serve.client_cert_allowed(cert, {"app.radon.internal"})
        assert not serve.client_cert_allowed(cert, {"ib-gateway-remote"})
        assert not serve.client_cert_allowed({}, {"radon-app"})
        assert not serve.client_cert_allowed(None, {"radon-app"})


class TestCertsScript:
    SCRIPT = Path(__file__).resolve().parents[2] / "cloud" / "scripts" / "ib-gateway-remote-certs.sh"

    def test_server_cert_is_not_a_client_cert(self):
        text = "\n".join(
            line for line in self.SCRIPT.read_text().splitlines() if not line.lstrip().startswith("#")
        )
        # One ext file per role: the server pair must not carry clientAuth.
        assert "serverAuth,clientAuth" not in text
        assert "extendedKeyUsage=serverAuth" in text
        assert "extendedKeyUsage=clientAuth" in text
        assert "DNS:radon-app" in text


# ---------------------------------------------------------------------------
# REL-172 (R-475): the 2FA-aware restart contract across the split.
# ---------------------------------------------------------------------------


def _lease_file(tmp_path: Path, *, holder: str = "radon-cloud.ib-watchdog", ttl: float = 300.0) -> Path:
    import json as _json
    import time as _time

    path = tmp_path / "lease" / "ib-2fa-push-lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    now = _time.time()
    path.write_text(
        _json.dumps(
            {
                "holder": holder,
                "acquired_at": now,
                "expires_at": now + ttl,
                "reason": "test",
                "port_down_since": None,
            }
        )
    )
    return path


class TestStatusCarriesBrokerLease:
    def test_status_reports_held_lease(self, tmp_path, monkeypatch):
        certs = mint_mtls(tmp_path)
        lease = _lease_file(tmp_path)
        monkeypatch.setenv("IB_2FA_LOCK_PATH", str(lease))
        httpd = _start(_config(tmp_path, certs, helper=_helper_script(tmp_path)))
        try:
            status, body = _call(httpd, certs, "/status")
            assert status == 200
            assert body["state"] == "running"
            assert body["lease"]["holder"] == "radon-cloud.ib-watchdog"
            assert body["lease"]["remaining_secs"] > 0
            assert body["transition"] is None
        finally:
            httpd.shutdown()

    def test_status_reports_no_lease_and_transition(self, tmp_path, monkeypatch):
        certs = mint_mtls(tmp_path)
        monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "absent" / "lock.json"))
        helper = _helper_script(tmp_path, rc=serve.CONTROL_BUSY_RC, stdout="transition-pending")
        httpd = _start(_config(tmp_path, certs, helper=helper))
        try:
            with pytest.raises(urllib.error.HTTPError) as err:
                _call(httpd, certs, "/status")
            assert err.value.code == 409
            body = json.loads(err.value.read().decode())
            assert body["state"] == "transition-pending"
            assert body["transition"] == "pending"
            assert body["lease"] is None
        finally:
            httpd.shutdown()


class TestVerbCooldown:
    """A caller cannot fire a second IBKR push seconds after clearing the
    only thing that would have refused it."""

    def _daemon(self, tmp_path):
        certs = mint_mtls(tmp_path)
        helper = _helper_script(tmp_path, stdout="ok")
        config = _config(tmp_path, certs, helper=helper)
        return certs, _start(config)

    def test_start_within_cooldown_of_stop_is_409(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serve, "_verb_history", {})
        certs, httpd = self._daemon(tmp_path)
        try:
            status, _ = _call(httpd, certs, "/stop", "POST")
            assert status == 200
            with pytest.raises(urllib.error.HTTPError) as err:
                _call(httpd, certs, "/start", "POST")
            assert err.value.code == 409
            body = json.loads(err.value.read().decode())
            assert "cooldown" in body["detail"]
        finally:
            httpd.shutdown()
        assert (tmp_path / "helper.log").read_text().splitlines() == ["stop"]

    @pytest.mark.parametrize("follow", ["restart", "start"])
    def test_restart_or_start_within_cooldown_of_reset_lease_is_409(self, tmp_path, monkeypatch, follow):
        monkeypatch.setattr(serve, "_verb_history", {})
        certs, httpd = self._daemon(tmp_path)
        try:
            status, _ = _call(httpd, certs, "/reset-lease", "POST")
            assert status == 200
            with pytest.raises(urllib.error.HTTPError) as err:
                _call(httpd, certs, f"/{follow}", "POST")
            assert err.value.code == 409
        finally:
            httpd.shutdown()
        assert (tmp_path / "helper.log").read_text().splitlines() == ["reset-lease"]

    def test_cooldown_expires_and_cannot_be_cleared_by_a_verb(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serve, "_verb_history", {})
        certs, httpd = self._daemon(tmp_path)
        try:
            _call(httpd, certs, "/stop", "POST")
            # reset-lease does not clear the stop->start cooldown.
            _call(httpd, certs, "/reset-lease", "POST")
            with pytest.raises(urllib.error.HTTPError) as err:
                _call(httpd, certs, "/start", "POST")
            assert err.value.code == 409
            # Only time clears it.
            for verb in list(serve._verb_history):
                serve._verb_history[verb] -= serve.VERB_COOLDOWN_S + 1
            status, _ = _call(httpd, certs, "/start", "POST")
            assert status == 200
        finally:
            httpd.shutdown()

    def test_status_and_stop_are_never_cooled_down(self, tmp_path, monkeypatch):
        monkeypatch.setattr(serve, "_verb_history", {})
        certs, httpd = self._daemon(tmp_path)
        try:
            _call(httpd, certs, "/start", "POST")
            status, _ = _call(httpd, certs, "/stop", "POST")
            assert status == 200
            _call(httpd, certs, "/status")
        finally:
            httpd.shutdown()
