"""Private-net IB Gateway control daemon.

Binds one RFC1918 or loopback address, requires mTLS, allowlists source IPs
as host addresses only, and execs ``radon-ib-gateway-control`` with a fixed
argv. No Docker, no shell, no FastAPI.

Routes:
  GET  /healthz      liveness, no helper
  GET  /status       helper status
  POST /start|stop|restart|reset-lease
"""
from __future__ import annotations

import ipaddress
import json
import os
import ssl
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable

# The lease module is stdlib-only (the helper itself runs it under the system
# interpreter); it lets /status report the broker's 2FA push lease to the app
# host, which cannot see this VM's lock file (REL-172, R-475).
try:
    from utils import ib_2fa_lock
except ImportError:  # pragma: no cover - `python -m scripts.ib_gateway_remote.serve`
    from scripts.utils import ib_2fa_lock

DEFAULT_BIND = "10.0.0.4"
DEFAULT_PORT = 8340
DEFAULT_ALLOW = "10.0.0.2"
DEFAULT_CLIENT_NAMES = "radon-app"
DEFAULT_HELPER = "/usr/local/bin/radon-ib-gateway-control"
HELPER_TIMEOUT_S = 120.0
# Per-connection socket timeout: covers the TLS handshake, the request line,
# headers and body. A peer that connects and goes quiet is released here
# instead of pinning a worker (R-471, R-494).
CONN_TIMEOUT_S = 10.0
# Every verb takes an empty body; anything larger than this is not a
# Gateway-control request (R-494).
MAX_BODY_BYTES = 4096
PRIVATE_NET = ipaddress.ip_network("10.0.0.0/16")
MUTATIONS = frozenset({"start", "stop", "restart", "reset-lease"})
VERBS = MUTATIONS | {"status"}
# REL-172 (R-475): a per-verb cooldown a caller cannot clear. `stop` releases
# the lease unconditionally and `reset-lease` exists to release it, so either
# followed by a fresh login within seconds stacks a second IBKR push behind
# one still in flight. Only time clears an entry; no verb does.
VERB_COOLDOWN_S = 60.0
_COOLDOWN_AFTER: dict[str, tuple[str, ...]] = {
    "start": ("stop", "reset-lease"),
    "restart": ("stop", "reset-lease"),
}
_verb_history: dict[str, float] = {}
_verb_history_guard = threading.Lock()
LEASE_HELD_RC = 75
CONTROL_BUSY_RC = 74
FORBIDDEN_BINDS = frozenset({"0.0.0.0", "::", "::0", ""})


class ConfigError(ValueError):
    """Startup misconfiguration. Process must exit, not listen."""


def parse_allowlist(raw: str) -> frozenset[str]:
    """Host addresses only. A subnet wider than /32 is a footgun on 10.0.0.0/16."""
    items = [part.strip() for part in raw.split(",") if part.strip()]
    if not items:
        raise ConfigError("RADON_IB_REMOTE_ALLOW is empty")
    out: set[str] = set()
    for item in items:
        if "/" in item:
            net = ipaddress.ip_network(item, strict=False)
            if net.num_addresses != 1:
                raise ConfigError(
                    f"allowlist must be host addresses, not a subnet: {item}"
                )
            addr = net.network_address
        else:
            addr = ipaddress.ip_address(item)
        if addr.is_unspecified:
            raise ConfigError(f"allowlist rejects unspecified: {item}")
        out.add(_canonical_ip(addr))
    return frozenset(out)


def _canonical_ip(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return str(addr.ipv4_mapped)
    return str(addr)


def parse_client_names(raw: str) -> frozenset[str]:
    """Client-cert names (CN or DNS SAN) that may drive the Gateway (R-495)."""
    names = frozenset(part.strip().lower() for part in raw.split(",") if part.strip())
    if not names:
        raise ConfigError("RADON_IB_REMOTE_CLIENT_NAMES is empty")
    return names


def client_cert_allowed(cert: dict | None, names: Iterable[str]) -> bool:
    """True when the verified peer certificate's CN or a DNS SAN is allowlisted."""
    if not cert:
        return False
    allowed = {n.lower() for n in names}
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName" and str(value).lower() in allowed:
                return True
    for kind, value in cert.get("subjectAltName", ()):
        if kind == "DNS" and str(value).lower() in allowed:
            return True
    return False


def bind_allowed(host: str) -> bool:
    if host in FORBIDDEN_BINDS:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if addr.is_unspecified or addr.is_multicast or addr.is_link_local:
        return False
    if addr.is_loopback:
        return True
    return addr in PRIVATE_NET


def load_config(env: dict[str, str] | None = None) -> dict:
    source = os.environ if env is None else env
    bind = (source.get("RADON_IB_REMOTE_BIND") or DEFAULT_BIND).strip()
    if not bind_allowed(bind):
        raise ConfigError(
            f"RADON_IB_REMOTE_BIND must be loopback or 10.0.0.0/16, not {bind!r}"
        )
    try:
        port = int(source.get("RADON_IB_REMOTE_PORT") or DEFAULT_PORT)
    except ValueError as exc:
        raise ConfigError("RADON_IB_REMOTE_PORT must be an integer") from exc
    if not (0 <= port <= 65535):
        raise ConfigError("RADON_IB_REMOTE_PORT out of range")
    allow = parse_allowlist(source.get("RADON_IB_REMOTE_ALLOW") or DEFAULT_ALLOW)
    client_names = parse_client_names(
        source.get("RADON_IB_REMOTE_CLIENT_NAMES") or DEFAULT_CLIENT_NAMES
    )
    helper = source.get("RADON_IB_GATEWAY_CONTROL") or DEFAULT_HELPER
    if not os.path.isabs(helper):
        raise ConfigError("helper path must be absolute")
    cert = Path(source.get("RADON_IB_REMOTE_CERT") or "")
    key = Path(source.get("RADON_IB_REMOTE_KEY") or "")
    ca = Path(source.get("RADON_IB_REMOTE_CA") or "")
    for label, path in (("cert", cert), ("key", key), ("ca", ca)):
        if not path.is_file():
            raise ConfigError(f"mTLS {label} missing: {path}")
    return {
        "bind": bind,
        "port": port,
        "allow": allow,
        "client_names": client_names,
        "helper": helper,
        "cert": str(cert),
        "key": str(key),
        "ca": str(ca),
        "timeout": HELPER_TIMEOUT_S,
        "conn_timeout": CONN_TIMEOUT_S,
    }


def ssl_context(cert: str, key: str, ca: str) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    ctx.load_verify_locations(ca)
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.check_hostname = False
    return ctx


def run_helper(helper: str, verb: str, timeout: float) -> tuple[int, str]:
    if verb not in VERBS:
        return 2, f"verb not allowed: {verb}"
    if not os.access(helper, os.X_OK):
        return 127, f"helper unavailable: {helper}"
    try:
        proc = subprocess.run(
            [helper, verb],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"helper timed out after {timeout:.0f}s"
    text = (proc.stderr or proc.stdout or "").strip()
    rc = proc.returncode if proc.returncode is not None else 1
    return rc, text


def cooldown_refusal(verb: str, now: float | None = None) -> str | None:
    """Reason a mutation is refused by the per-verb cooldown, else None."""
    now = time.monotonic() if now is None else now
    with _verb_history_guard:
        for prior in _COOLDOWN_AFTER.get(verb, ()):
            at = _verb_history.get(prior)
            if at is None:
                continue
            elapsed = now - at
            if elapsed < VERB_COOLDOWN_S:
                return (
                    f"cooldown: {prior} ran {elapsed:.0f}s ago; {verb} allowed in "
                    f"{VERB_COOLDOWN_S - elapsed:.0f}s (a fresh login now would stack a 2FA push)"
                )
    return None


def record_verb(verb: str, now: float | None = None) -> None:
    with _verb_history_guard:
        _verb_history[verb] = time.monotonic() if now is None else now


def broker_lease() -> dict | None:
    """The 2FA push lease on disk, or None. Cheap read: no orphan probe."""
    try:
        with ib_2fa_lock._guard(exclusive=False, timeout_secs=1.0):
            lock = ib_2fa_lock._read_lock_file()
    except Exception:  # noqa: BLE001 - status must never fail on the lease read
        return None
    if lock is None:
        return None
    now = time.time()
    if lock.is_expired(now):
        return None
    return {
        "holder": lock.holder,
        "acquired_at": lock.acquired_at,
        "expires_at": lock.expires_at,
        "remaining_secs": max(0, int(lock.expires_at - now)),
        "reason": lock.reason,
    }


def peer_allowed(peer: str, allow: Iterable[str]) -> bool:
    try:
        addr = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return _canonical_ip(addr) in set(allow)


class GatewayRemoteHandler(BaseHTTPRequestHandler):
    server_version = "radon-ib-gateway-remote/1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("ib-gateway-remote: " + (fmt % args) + "\n")

    def setup(self) -> None:
        # StreamRequestHandler applies self.timeout to the connection; the
        # handshake already ran under the same bound in finish_request.
        self.timeout = self.server.gateway_config["conn_timeout"]
        super().setup()

    def _peer(self) -> str:
        host = self.client_address[0]
        try:
            return _canonical_ip(ipaddress.ip_address(host))
        except ValueError:
            return host

    def _refuse(self, code: int, reason: str) -> None:
        body = json.dumps({"ok": False, "error": reason}).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _authorize(self) -> bool:
        # TCP peer only. Forwarded headers are attacker-controlled. The server
        # already dropped disallowed peers before the handshake; this is the
        # belt for a handler constructed any other way.
        cfg = self.server.gateway_config
        if not peer_allowed(self._peer(), cfg["allow"]):
            self._refuse(403, "source not allowlisted")
            return False
        try:
            cert = self.connection.getpeercert()
        except (ssl.SSLError, OSError, AttributeError):
            cert = None
        if not client_cert_allowed(cert, cfg["client_names"]):
            self._refuse(403, "client certificate not allowlisted")
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorize():
            return
        if self.path.split("?", 1)[0] == "/healthz":
            self._ok({"ok": True, "service": "ib-gateway-remote"})
            return
        if self.path.split("?", 1)[0] == "/status":
            self._helper("status")
            return
        self._refuse(404, "not found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorize():
            return
        path = self.path.split("?", 1)[0]
        verb = path[1:] if path.startswith("/") else path
        if verb not in MUTATIONS:
            self._refuse(404, "not found")
            return
        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._refuse(400, "bad Content-Length")
            return
        if length < 0 or length > MAX_BODY_BYTES:
            self.close_connection = True
            self._refuse(413, f"body exceeds {MAX_BODY_BYTES} bytes")
            return
        if length:
            self.rfile.read(length)
        self._helper(verb)

    def _helper(self, verb: str) -> None:
        cfg = self.server.gateway_config
        if verb in MUTATIONS:
            refusal = cooldown_refusal(verb)
            if refusal is not None:
                self._ok({"ok": False, "verb": verb, "returncode": CONTROL_BUSY_RC, "detail": refusal}, 409)
                return
        rc, detail = run_helper(cfg["helper"], verb, cfg["timeout"])
        if verb == "status":
            state = "running" if rc == 0 and detail.strip() == "running" else (
                "stopped" if "stopped" in detail else "unknown"
            )
            if detail.strip() in {"running", "stopped", "unknown", "transition-pending"}:
                state = detail.strip()
            payload = {
                "ok": rc == 0 or state == "stopped",
                "state": state,
                "detail": detail,
                "returncode": rc,
                "lease": broker_lease(),
                "transition": "pending" if state == "transition-pending" else None,
            }
            self._ok(payload, 409 if rc in {LEASE_HELD_RC, CONTROL_BUSY_RC} else 200)
            return
        if rc in {LEASE_HELD_RC, CONTROL_BUSY_RC}:
            self._ok({"ok": False, "verb": verb, "returncode": rc, "detail": detail}, 409)
            return
        if rc == 0:
            record_verb(verb)
            self._ok({"ok": True, "verb": verb, "detail": detail, "returncode": 0})
            return
        self._ok({"ok": False, "verb": verb, "detail": detail, "returncode": rc}, 502)


class QuietTLSServer(ThreadingHTTPServer):
    """The listening socket stays plain TCP. Each accepted connection is
    wrapped and handshaken on its own worker thread under CONN_TIMEOUT_S, so a
    peer that never sends ClientHello pins nothing but its own thread
    (R-471). Disallowed peers are dropped before the handshake."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, config: dict, ssl_ctx: ssl.SSLContext):
        self.gateway_config = config
        self.ssl_ctx = ssl_ctx
        super().__init__(addr, handler)

    def verify_request(self, request, client_address) -> bool:
        try:
            peer = _canonical_ip(ipaddress.ip_address(client_address[0]))
        except ValueError:
            peer = client_address[0]
        if peer_allowed(peer, self.gateway_config["allow"]):
            return True
        sys.stderr.write(f"ib-gateway-remote: dropped {client_address} before handshake\n")
        return False

    def finish_request(self, request, client_address) -> None:
        request.settimeout(self.gateway_config["conn_timeout"])
        try:
            tls = self.ssl_ctx.wrap_socket(
                request, server_side=True, do_handshake_on_connect=False
            )
        except (ssl.SSLError, OSError):
            self.handle_error(request, client_address)
            return
        try:
            tls.do_handshake()
        except (ssl.SSLError, OSError, TimeoutError):
            self.handle_error(request, client_address)
            tls.close()
            return
        try:
            super().finish_request(tls, client_address)
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def handle_error(self, request, client_address) -> None:
        sys.stderr.write(f"ib-gateway-remote: client error {client_address}\n")


def make_server(config: dict, *, port: int | None = None) -> QuietTLSServer:
    bind_port = config["port"] if port is None else port
    ctx = ssl_context(config["cert"], config["key"], config["ca"])
    return QuietTLSServer((config["bind"], bind_port), GatewayRemoteHandler, config, ctx)


def main() -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"REFUSING ib-gateway-remote: {exc}", file=sys.stderr)
        return 2
    httpd = make_server(config)
    print(
        f"ib-gateway-remote listening {config['bind']}:{httpd.server_address[1]}",
        file=sys.stderr,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
