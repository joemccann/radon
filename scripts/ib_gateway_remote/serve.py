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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable

DEFAULT_BIND = "10.0.0.4"
DEFAULT_PORT = 8340
DEFAULT_ALLOW = "10.0.0.2"
DEFAULT_HELPER = "/usr/local/bin/radon-ib-gateway-control"
HELPER_TIMEOUT_S = 120.0
PRIVATE_NET = ipaddress.ip_network("10.0.0.0/16")
MUTATIONS = frozenset({"start", "stop", "restart", "reset-lease"})
VERBS = MUTATIONS | {"status"}
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
        "helper": helper,
        "cert": str(cert),
        "key": str(key),
        "ca": str(ca),
        "timeout": HELPER_TIMEOUT_S,
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
        # TCP peer only. Forwarded headers are attacker-controlled.
        if not peer_allowed(self._peer(), self.server.gateway_config["allow"]):
            self._refuse(403, "source not allowlisted")
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
        length = int(self.headers.get("Content-Length") or "0")
        if length:
            self.rfile.read(length)
        self._helper(verb)

    def _helper(self, verb: str) -> None:
        cfg = self.server.gateway_config
        rc, detail = run_helper(cfg["helper"], verb, cfg["timeout"])
        if rc in {LEASE_HELD_RC, CONTROL_BUSY_RC}:
            self._ok({"ok": False, "verb": verb, "returncode": rc, "detail": detail}, 409)
            return
        if verb == "status":
            state = "running" if rc == 0 and detail.strip() == "running" else (
                "stopped" if "stopped" in detail else "unknown"
            )
            if detail.strip() in {"running", "stopped", "unknown", "transition-pending"}:
                state = detail.strip()
            self._ok({"ok": rc == 0 or state == "stopped", "state": state, "detail": detail, "returncode": rc})
            return
        if rc == 0:
            self._ok({"ok": True, "verb": verb, "detail": detail, "returncode": 0})
            return
        self._ok({"ok": False, "verb": verb, "detail": detail, "returncode": rc}, 502)


class QuietTLSServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, config: dict):
        self.gateway_config = config
        super().__init__(addr, handler)

    def handle_error(self, request, client_address) -> None:
        sys.stderr.write(f"ib-gateway-remote: client error {client_address}\n")


def make_server(config: dict, *, port: int | None = None) -> QuietTLSServer:
    bind_port = config["port"] if port is None else port
    httpd = QuietTLSServer((config["bind"], bind_port), GatewayRemoteHandler, config)
    ctx = ssl_context(config["cert"], config["key"], config["ca"])
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd


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
