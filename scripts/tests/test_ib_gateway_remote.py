"""Adversarial tests for the broker-local IB Gateway remote-control daemon."""
from __future__ import annotations

import errno
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from ib_gateway_remote import serve

REPO = Path(__file__).resolve().parents[2]


def _openssl_unusable() -> str | None:
    """Reason this host's openssl cannot mint the test PKI, else None (T-370)."""
    if shutil.which("openssl") is None:
        return "no `openssl` binary on PATH"
    # Functional probe: actually exercise -addext (LibreSSL and OpenSSL < 1.1.1
    # reject it), not a spoofable help-text grep.
    probe = subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", "1", "-subj", "/CN=addext-probe",
            "-addext", "basicConstraints=CA:TRUE",
            "-keyout", os.devnull, "-out", os.devnull,
        ],
        capture_output=True, text=True, check=False,
    )
    if probe.returncode != 0:
        first = (probe.stderr or probe.stdout).strip().splitlines()
        return (
            "`openssl req` cannot mint certs with -addext "
            f"(LibreSSL / OpenSSL < 1.1.1?): {first[0] if first else 'no output'}"
        )
    return None


@pytest.fixture(scope="session")
def certs(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    """One RSA-2048 test PKI (CA, server, client, rogue) per session (T-370)."""
    reason = _openssl_unusable()
    if reason is not None:
        pytest.skip(
            f"cannot mint the mTLS test PKI: {reason}. Consequence: the IB "
            "Gateway remote-control mTLS gate tests (rogue / missing client "
            "cert, plaintext refusal, allowlist) did NOT run on this host."
        )
    return mint_mtls(tmp_path_factory.mktemp("mtls"))


def _openssl(*args: str) -> None:
    result = subprocess.run(
        ["openssl", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def mint_mtls(tmp_path: Path) -> dict[str, Path]:
    ca_key = tmp_path / "ca.key"
    ca_pem = tmp_path / "ca.pem"
    server_key = tmp_path / "server.key"
    server_csr = tmp_path / "server.csr"
    server_pem = tmp_path / "server.pem"
    client_key = tmp_path / "client.key"
    client_csr = tmp_path / "client.csr"
    client_pem = tmp_path / "client.pem"
    rogue_key = tmp_path / "rogue.key"
    rogue_csr = tmp_path / "rogue.csr"
    rogue_pem = tmp_path / "rogue.pem"
    rogue_ca_key = tmp_path / "rogue-ca.key"
    rogue_ca = tmp_path / "rogue-ca.pem"
    ca_ext = tmp_path / "ca.cnf"
    ca_ext.write_text(
        "basicConstraints=critical,CA:TRUE\n"
        "keyUsage=critical,keyCertSign,cRLSign\n"
    )
    ext = tmp_path / "san.cnf"
    ext.write_text(
        "subjectAltName=IP:127.0.0.1,IP:10.0.0.4\n"
        "extendedKeyUsage=serverAuth,clientAuth\n"
        "keyUsage=digitalSignature,keyEncipherment\n"
    )
    _openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=radon-ib-remote-ca",
        "-keyout", str(ca_key), "-out", str(ca_pem),
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
    )
    for key, csr, pem, cn in (
        (server_key, server_csr, server_pem, "server"),
        (client_key, client_csr, client_pem, "client"),
    ):
        _openssl(
            "req", "-newkey", "rsa:2048", "-nodes",
            "-subj", f"/CN={cn}",
            "-keyout", str(key), "-out", str(csr),
        )
        _openssl(
            "x509", "-req", "-days", "1", "-in", str(csr),
            "-CA", str(ca_pem), "-CAkey", str(ca_key), "-CAcreateserial",
            "-out", str(pem), "-extfile", str(ext),
        )
    _openssl(
        "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-subj", "/CN=rogue-ca",
        "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign",
        "-keyout", str(rogue_ca_key), "-out", str(rogue_ca),
    )
    _openssl(
        "req", "-newkey", "rsa:2048", "-nodes",
        "-subj", "/CN=rogue",
        "-keyout", str(rogue_key), "-out", str(rogue_csr),
    )
    _openssl(
        "x509", "-req", "-days", "1", "-in", str(rogue_csr),
        "-CA", str(rogue_ca), "-CAkey", str(rogue_ca_key), "-CAcreateserial",
        "-out", str(rogue_pem), "-extfile", str(ext),
    )
    return {
        "ca": ca_pem,
        "cert": server_pem,
        "key": server_key,
        "client_cert": client_pem,
        "client_key": client_key,
        "rogue_cert": rogue_pem,
        "rogue_key": rogue_key,
        "rogue_ca": rogue_ca,
    }


def _helper_script(tmp_path: Path, *, rc: int = 0, stdout: str = "running") -> Path:
    path = tmp_path / "radon-ib-gateway-control"
    path.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$1\" >> '{tmp_path / 'helper.log'}'\n"
        f"printf '%s\\n' '{stdout}'\n"
        f"exit {rc}\n"
    )
    path.chmod(0o755)
    return path


def _config(tmp_path: Path, certs: dict[str, Path], **overrides) -> dict:
    helper = overrides.pop("helper", None) or _helper_script(tmp_path)
    env = {
        "RADON_IB_REMOTE_BIND": "127.0.0.1",
        "RADON_IB_REMOTE_PORT": "0",
        "RADON_IB_REMOTE_ALLOW": "127.0.0.1",
        # REL-168 (R-495): the daemon allowlists the client cert's CN/DNS SAN;
        # mint_mtls issues the client pair as CN=client.
        "RADON_IB_REMOTE_CLIENT_NAMES": "client",
        "RADON_IB_REMOTE_CERT": str(certs["cert"]),
        "RADON_IB_REMOTE_KEY": str(certs["key"]),
        "RADON_IB_REMOTE_CA": str(certs["ca"]),
        "RADON_IB_GATEWAY_CONTROL": str(helper),
    }
    env.update({k: str(v) for k, v in overrides.items()})
    return serve.load_config(env)


def _start(config: dict):
    httpd = serve.make_server(config, port=0)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _stop(httpd) -> None:
    """Stop serve_forever AND close the listening socket (T-370)."""
    httpd.shutdown()
    httpd.server_close()


def _ctx(certs: dict[str, Path], *, client: bool = True, rogue: bool = False) -> ssl.SSLContext:
    # T-359: ALWAYS trust the real CA for server verification — including the
    # rogue-client ctx. Trusting rogue_ca there made urlopen fail on the
    # SERVER cert before the server ever evaluated the client cert, so a
    # server that also trusted the rogue CA slipped past the rogue test.
    ctx = ssl.create_default_context(cafile=str(certs["ca"]))
    ctx.check_hostname = False
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if rogue:
        ctx.load_cert_chain(str(certs["rogue_cert"]), str(certs["rogue_key"]))
    elif client:
        ctx.load_cert_chain(str(certs["client_cert"]), str(certs["client_key"]))
    return ctx


def _call(httpd, certs, path: str, method: str = "GET", *, ctx=None, headers=None):
    port = httpd.server_address[1]
    req = urllib.request.Request(
        f"https://127.0.0.1:{port}{path}",
        method=method,
        data=b"" if method == "POST" else None,
        headers=headers or {},
    )
    with urllib.request.urlopen(req, context=ctx or _ctx(certs), timeout=5) as resp:
        return resp.status, json.loads(resp.read().decode())


def _assert_mtls_handshake_rejected(exc: BaseException) -> None:
    """Pin a CERT_REQUIRED reject that dies before the HTTP handler.

    Some OpenSSL builds surface ssl.SSLError. Others RST after
    QuietTLSServer.finish_request catches the handshake failure and
    closes, so urllib raises URLError([Errno 104] Connection reset by
    peer) — CI scripts-i 2026-09-02. HTTPError means the handshake
    completed and the handler ran.
    """
    assert not isinstance(exc, urllib.error.HTTPError), (
        "handshake succeeded; request reached the handler"
    )
    reason = getattr(exc, "reason", exc)
    if isinstance(exc, ssl.SSLError) or isinstance(reason, ssl.SSLError):
        return
    if _is_connection_reset(exc) or _is_connection_reset(reason):
        return
    raise AssertionError(f"not a handshake reject: {type(exc).__name__}: {exc!r}")


def _is_connection_reset(exc: object) -> bool:
    if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
        return True
    return isinstance(exc, OSError) and exc.errno in {
        errno.ECONNRESET,
        errno.ECONNABORTED,
        errno.EPIPE,
    }


class TestStdlibOnlyIsolation:
    def test_import_pulls_in_no_trading_stack(self):
        forbidden = {"ib_insync", "uvicorn", "fastapi", "starlette", "libsql", "ibapi"}
        code = (
            "import sys; import ib_gateway_remote.serve;\n"
            "bad = sorted(m for m in sys.modules\n"
            "  if m.split('.')[0] in %r\n"
            "  or m.startswith('scripts.api') or m.startswith('api.')\n"
            "  or m.startswith('scripts.db'));\n"
            "print(','.join(bad)); sys.exit(1 if bad else 0)" % (forbidden,)
        )
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(["scripts", "."])}
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=env,
            cwd=str(REPO),
            timeout=30,
        )
        assert result.returncode == 0, result.stdout + result.stderr


class TestConfig:
    def test_refuses_unspecified_bind(self, tmp_path, monkeypatch, certs):
        with pytest.raises(serve.ConfigError, match="10.0.0.0/16"):
            _config(tmp_path, certs, RADON_IB_REMOTE_BIND="0.0.0.0")

    def test_refuses_public_bind(self, tmp_path, certs):
        with pytest.raises(serve.ConfigError):
            _config(tmp_path, certs, RADON_IB_REMOTE_BIND="1.1.1.1")

    def test_allows_private_and_loopback(self):
        assert serve.bind_allowed("10.0.0.4") is True
        assert serve.bind_allowed("127.0.0.1") is True
        assert serve.bind_allowed("0.0.0.0") is False
        assert serve.bind_allowed("::") is False

    def test_refuses_subnet_allowlist(self, tmp_path, certs):
        with pytest.raises(serve.ConfigError, match="host addresses"):
            _config(tmp_path, certs, RADON_IB_REMOTE_ALLOW="10.0.0.0/16")

    def test_refuses_relative_helper(self, tmp_path, certs):
        with pytest.raises(serve.ConfigError, match="absolute"):
            _config(tmp_path, certs, RADON_IB_GATEWAY_CONTROL="ib-gateway-control")

    def test_refuses_missing_certs(self, tmp_path):
        with pytest.raises(serve.ConfigError, match="mTLS"):
            serve.load_config({
                "RADON_IB_REMOTE_BIND": "127.0.0.1",
                "RADON_IB_REMOTE_ALLOW": "127.0.0.1",
                "RADON_IB_REMOTE_CERT": str(tmp_path / "missing.pem"),
                "RADON_IB_REMOTE_KEY": str(tmp_path / "missing.key"),
                "RADON_IB_REMOTE_CA": str(tmp_path / "missing-ca.pem"),
                "RADON_IB_GATEWAY_CONTROL": str(_helper_script(tmp_path)),
            })


class TestDaemon:
    def test_status_and_restart_exec_helper_only(self, tmp_path, certs):
        helper = _helper_script(tmp_path)
        config = _config(tmp_path, certs, helper=helper)
        httpd = _start(config)
        try:
            status, body = _call(httpd, certs, "/status")
            assert status == 200
            assert body["state"] == "running"
            status, body = _call(httpd, certs, "/restart", "POST")
            assert status == 200
            assert body["ok"] is True
        finally:
            _stop(httpd)
        log = (tmp_path / "helper.log").read_text().splitlines()
        assert log == ["status", "restart"]

    def test_unknown_verb_does_not_touch_helper(self, tmp_path, certs):
        helper = _helper_script(tmp_path)
        httpd = _start(_config(tmp_path, certs, helper=helper))
        try:
            with pytest.raises(urllib.error.HTTPError) as err:
                _call(httpd, certs, "/restart-preheld", "POST")
            assert err.value.code == 404
            with pytest.raises(urllib.error.HTTPError) as err:
                _call(httpd, certs, "/docker", "POST")
            assert err.value.code == 404
        finally:
            _stop(httpd)
        assert not (tmp_path / "helper.log").exists()

    def test_lease_held_is_409(self, tmp_path, certs):
        helper = _helper_script(tmp_path, rc=75, stdout="held holder=watchdog")
        config = _config(tmp_path, certs, helper=helper)
        httpd = _start(config)
        try:
            with pytest.raises(urllib.error.HTTPError) as err:
                _call(httpd, certs, "/restart", "POST")
            assert err.value.code == 409
            payload = json.loads(err.value.read().decode())
            assert payload["returncode"] == 75
        finally:
            _stop(httpd)

    def test_forwarded_for_cannot_spoof_allowlist(self, tmp_path, certs):
        helper = _helper_script(tmp_path)
        config = _config(
            tmp_path, certs, helper=helper, RADON_IB_REMOTE_ALLOW="10.0.0.2"
        )
        httpd = _start(config)
        try:
            # REL-168 (R-471): a disallowed peer is dropped BEFORE the TLS
            # handshake, so the header never reaches the daemon at all. The
            # original 403 shape is kept as the accepted alternative for a
            # handler constructed any other way.
            with pytest.raises((urllib.error.HTTPError, urllib.error.URLError, ssl.SSLError, OSError)) as err:
                _call(
                    httpd,
                    certs,
                    "/restart",
                    "POST",
                    headers={"X-Forwarded-For": "10.0.0.2"},
                )
            if isinstance(err.value, urllib.error.HTTPError):
                assert err.value.code == 403
        finally:
            _stop(httpd)
        assert not (tmp_path / "helper.log").exists()

    def test_missing_client_cert_is_tls_failure(self, tmp_path, certs):
        httpd = _start(_config(tmp_path, certs))
        try:
            ctx = _ctx(certs, client=False)
            with pytest.raises((ssl.SSLError, urllib.error.URLError, OSError)) as err:
                _call(httpd, certs, "/healthz", ctx=ctx)
            _assert_mtls_handshake_rejected(err.value)
        finally:
            _stop(httpd)

    def test_connection_reset_is_a_handshake_reject_not_an_http_response(self):
        _assert_mtls_handshake_rejected(ssl.SSLError("CERTIFICATE_REQUIRED"))
        _assert_mtls_handshake_rejected(
            urllib.error.URLError(ConnectionResetError(errno.ECONNRESET, "Connection reset by peer"))
        )
        _assert_mtls_handshake_rejected(
            urllib.error.URLError(OSError(errno.ECONNRESET, "Connection reset by peer"))
        )
        with pytest.raises(AssertionError, match="handshake succeeded"):
            _assert_mtls_handshake_rejected(
                urllib.error.HTTPError(
                    "https://127.0.0.1/healthz", 403, "Forbidden", hdrs=None, fp=None
                )
            )
        with pytest.raises(AssertionError, match="not a handshake reject"):
            _assert_mtls_handshake_rejected(urllib.error.URLError(TimeoutError("timed out")))

    def test_rogue_client_cert_is_tls_failure(self, tmp_path, certs):
        httpd = _start(_config(tmp_path, certs))
        try:
            # The rogue ctx verifies the SERVER cert fine (real CA in its
            # trust store), so the only failure left is the SERVER rejecting
            # the rogue client cert at the handshake. SSLError ONLY: an
            # HTTPError/URLError here would mean the handshake succeeded and
            # the request reached the handler — a server trusting the rogue
            # CA must fail this test.
            with pytest.raises(ssl.SSLError):
                _call(httpd, certs, "/restart", "POST", ctx=_ctx(certs, rogue=True))
        finally:
            _stop(httpd)

    def test_plaintext_http_is_not_spoken(self, tmp_path, certs):
        httpd = _start(_config(tmp_path, certs))
        port = httpd.server_address[1]
        try:
            with pytest.raises((ssl.SSLError, urllib.error.URLError, OSError)):
                urllib.request.urlopen(f"http://127.0.0.1:{port}/restart", timeout=2)
        finally:
            _stop(httpd)

    def test_healthz_does_not_exec_helper(self, tmp_path, certs):
        helper = _helper_script(tmp_path)
        httpd = _start(_config(tmp_path, certs, helper=helper))
        try:
            status, body = _call(httpd, certs, "/healthz")
            assert status == 200
            assert body["ok"] is True
        finally:
            _stop(httpd)
        assert not (tmp_path / "helper.log").exists()


class TestHelperArgv:
    def test_run_helper_propagates_lease_rc(self, tmp_path):
        helper = _helper_script(tmp_path, rc=75, stdout="held holder=watchdog")
        rc, detail = serve.run_helper(str(helper), "restart", 2)
        assert rc == 75
        assert "held" in detail

    def test_run_helper_rejects_unknown_verb_without_spawn(self, tmp_path, monkeypatch):
        helper = _helper_script(tmp_path)
        rc, detail = serve.run_helper(str(helper), "restart-preheld", 1)
        assert rc == 2
        assert "not allowed" in detail
        assert not (tmp_path / "helper.log").exists()

    def test_peer_allow_normalizes_ipv4_mapped(self):
        allow = serve.parse_allowlist("127.0.0.1")
        assert serve.peer_allowed("127.0.0.1", allow)
        assert serve.peer_allowed("::ffff:127.0.0.1", allow)
        assert not serve.peer_allowed("10.0.0.4", allow)
