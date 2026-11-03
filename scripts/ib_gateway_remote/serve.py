"""Private-net IB Gateway control daemon.

Binds one RFC1918 or loopback address, requires mTLS, allowlists source IPs
as host addresses only, and execs ``radon-ib-gateway-control`` with a fixed
argv. No Docker, no shell, no FastAPI.

Routes:
  GET  /healthz      liveness, no helper
  GET  /status       helper status
  POST /start|stop|restart|reset-lease
  POST /hold|unhold   IBKR operator hold (JSON body: reason, actor)
  POST /rotate-password  IB Gateway password rotation (JSON body: password,
                         userid?, actor?). Writes the new value through the
                         root writer (`sudo -n radon-ib-gateway-credential
                         write`, value on stdin), then one helper `restart`.
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
    from utils import ib_2fa_lock, ib_operator_hold
    from utils.ib_login_throttle import format_utc, login_throttle_gate
except ImportError:  # pragma: no cover - `python -m scripts.ib_gateway_remote.serve`
    from scripts.utils import ib_2fa_lock, ib_operator_hold
    from scripts.utils.ib_login_throttle import format_utc, login_throttle_gate

DEFAULT_BIND = "10.0.0.4"
DEFAULT_PORT = 8340
DEFAULT_ALLOW = "10.0.0.2"
DEFAULT_CLIENT_NAMES = "radon-app"
DEFAULT_HELPER = "/usr/local/bin/radon-ib-gateway-control"
DEFAULT_WATCHDOG_STATE = "/var/lib/radon/ib-watchdog-state.json"
DEFAULT_CREDENTIAL_WRITER = "/usr/local/sbin/radon-ib-gateway-credential"
DEFAULT_SUDO = "/usr/bin/sudo"
# Verbs that start a Gateway login. Inside an IBKR login throttle each one is
# another failed attempt (2026-09-26), so they wait out the watchdog's quiet
# period; stop and reset-lease never log in.
LOGIN_VERBS = frozenset({"start", "restart"})
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
# The IBKR operator hold, set and cleared from the admin panel. Not helper
# verbs: the daemon writes the hold itself (utils/ib_operator_hold.py), then
# runs the helper's own `stop` / `start`. `hold` never logs in, so no cooldown
# or throttle gates it; `unhold` logs in once and both gate that login.
HOLD_VERBS = frozenset({"hold", "unhold"})
HOLD_REASON_MAX = 200
HOLD_ACTOR_MAX = 80
# IB Gateway password rotation. Not a helper verb either: run_helper never
# sees it. The daemon calls the root writer itself (password on stdin; the
# helper's own `python - <<'PY'` re-execs replace fd 0, so it cannot carry a
# secret), then runs the helper's `restart` exactly once. No retry: a wrong
# password retried is how an IBKR account gets locked.
ROTATE_VERB = "rotate-password"
ROTATE_FIELDS = frozenset({"password", "userid", "actor"})
WRITER_TIMEOUT_S = 10.0
GATEWAY_VALUE_MAX = 256
# Helper exits that mean "not now", not "broken": operator hold (73), control
# busy (74), push lease held (75). After a write they defer the restart.
HELPER_DEFER_RCS = frozenset({73, 74, 75})
# REL-172 (R-475): a per-verb cooldown a caller cannot clear. `stop` releases
# the lease unconditionally and `reset-lease` exists to release it, so either
# followed by a fresh login within seconds stacks a second IBKR push behind
# one still in flight. Only time clears an entry; no verb does.
VERB_COOLDOWN_S = 60.0
_COOLDOWN_AFTER: dict[str, tuple[str, ...]] = {
    "start": ("stop", "reset-lease", ROTATE_VERB),
    "restart": ("stop", "reset-lease", ROTATE_VERB),
    # A rotation ends in a Gateway restart (one push). It waits out the same
    # stop/reset-lease window, a prior restart, and a prior rotation.
    ROTATE_VERB: ("stop", "reset-lease", "restart", ROTATE_VERB),
}
_rotate_guard = threading.Lock()
_verb_history: dict[str, float] = {}
_verb_history_guard = threading.Lock()
# Gateway mutations admitted by admit_verb and not yet finished. Claimed and
# released under _verb_history_guard together with the cooldown check and the
# record, so two daemon threads can never both pass the check (audit
# 2026-11-03 §3.2).
_verbs_in_flight: set[str] = set()
# Helper exits that mean it refused before touching the Gateway: usage (2),
# operator hold (73), control busy (74), lease held (75), helper missing
# (127). Every other exit, failures and timeouts included, may already have
# started a login and sent an IBKR push, so it arms the cooldown.
HELPER_NOT_ATTEMPTED_RCS = frozenset({2, 73, 74, 75, 127})


def _verb_state_file() -> Path | None:
    """REL-200 (R-567): the cooldown survives a daemon restart via a small
    wall-clock state file (systemd StateDirectory, or the test override)."""
    root = os.environ.get("STATE_DIRECTORY") or os.environ.get("RADON_IB_REMOTE_STATE_DIR")
    if not root:
        return None
    return Path(root.split(":", 1)[0]) / "verb-history.json"


def _persist_verb_history(wall_by_verb: dict[str, float]) -> None:
    path = _verb_state_file()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(wall_by_verb))
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError:  # noqa: PERF203 - best-effort; the in-memory copy still guards
        pass


_verb_wall_history: dict[str, float] = {}


def _hydrate_verb_history() -> None:
    """Seed the monotonic history from persisted wall-clock stamps that are
    still inside the cooldown window."""
    path = _verb_state_file()
    if path is None or not path.is_file():
        return
    try:
        stored = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if not isinstance(stored, dict):
        return
    wall_now = time.time()
    mono_now = time.monotonic()
    with _verb_history_guard:
        for verb, wall_at in stored.items():
            if not isinstance(wall_at, (int, float)):
                continue
            age = wall_now - wall_at
            if 0 <= age < VERB_COOLDOWN_S:
                _verb_history[verb] = mono_now - age
                _verb_wall_history[verb] = wall_at


_hydrate_verb_history()
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


def healthz_payload() -> dict:
    """Liveness plus mTLS cert expiry (REL-178 / R-496): the 825-day
    self-signed pair dies silently otherwise."""
    payload: dict = {"ok": True, "service": "ib-gateway-remote"}
    cert_path = os.environ.get("RADON_IB_REMOTE_CERT", "")
    if cert_path:
        try:
            from utils.cert_expiry import cert_days_left, cert_not_after
        except ImportError:  # pragma: no cover - module entrypoint layout
            from scripts.utils.cert_expiry import cert_days_left, cert_not_after
        days = cert_days_left(cert_path)
        if days is not None:
            payload["ib_remote_cert_days_left"] = round(days, 1)
            payload["ib_remote_cert_not_after"] = cert_not_after(cert_path)
    return payload


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
    writer = source.get("RADON_IB_CREDENTIAL_WRITER") or DEFAULT_CREDENTIAL_WRITER
    sudo = source.get("RADON_IB_REMOTE_SUDO") or DEFAULT_SUDO
    if not os.path.isabs(writer) or not os.path.isabs(sudo):
        raise ConfigError("credential writer and sudo paths must be absolute")
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
        "writer": writer,
        "sudo": sudo,
        "watchdog_state": source.get("RADON_IB_WATCHDOG_STATE_PATH") or DEFAULT_WATCHDOG_STATE,
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


def _gateway_value_error(value: object) -> str | None:
    """Why a Gateway credential cannot be written as KEY='value', else None.

    Same rule as value_error() in cloud/scripts/radon-ib-gateway-credential.py
    and _gateway_value_error() in scripts/api/routes/credentials.py.
    """
    if not isinstance(value, str):
        return "must be a string"
    if not value:
        return "must not be empty"
    if len(value) > GATEWAY_VALUE_MAX:
        return f"must be at most {GATEWAY_VALUE_MAX} characters"
    if value != value.strip():
        return "must not start or end with whitespace"
    if "'" in value:
        return "must not contain a single quote"
    # C0, DEL and C1 (U+0085 NEL is C1), then U+2028/U+2029, the only Zl/Zp
    # code points. The writer splits the file on \n only, but
    # str.splitlines() breaks on all of these, so a stored one could split
    # the line on a later rewrite and inject its tail as a KEY=value line.
    if any(ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F for ch in value):
        return "must not contain control characters"
    if "\u2028" in value or "\u2029" in value or len((value + "x").splitlines()) != 1:
        return "must not contain line separators"
    if value.endswith("\\"):
        return "must not end with a backslash"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "must be valid UTF-8 text"
    return None


def scrub(text: str, secrets: Iterable[str]) -> str:
    """Replace every submitted value in helper/writer output."""
    out = text or ""
    for secret in sorted({s for s in secrets if s}, key=len, reverse=True):
        out = out.replace(secret, "[redacted]")
    return out


def run_credential_writer(cfg: dict, values: dict) -> tuple[int | None, str]:
    """`sudo -n <writer> write` with the values as JSON on stdin.

    Returns (returncode, output); returncode None means the writer timed out
    and the outcome is unknown.
    """
    try:
        proc = subprocess.run(
            [cfg["sudo"], "-n", cfg["writer"], "write"],
            input=json.dumps(values),
            text=True,
            capture_output=True,
            timeout=WRITER_TIMEOUT_S,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"credential writer timed out after {WRITER_TIMEOUT_S:.0f}s"
    except OSError:
        return 127, "credential writer could not be started"
    return proc.returncode, (proc.stderr or proc.stdout or "").strip()


def cooldown_refusal(verb: str, now: float | None = None) -> str | None:
    """Reason a mutation is refused by the per-verb cooldown, else None.
    Read-only; ``admit_verb`` is the check-and-claim callers must use."""
    with _verb_history_guard:
        return _cooldown_refusal_locked(verb, now)


def _cooldown_refusal_locked(verb: str, now: float | None) -> str | None:
    now = time.monotonic() if now is None else now
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


def admit_verb(verb: str, now: float | None = None) -> str | None:
    """Atomically check the cooldown and claim ``verb`` as in flight.

    Returns the refusal reason, or None when admitted; an admitted verb MUST
    be finished with ``finish_verb`` (try/finally). One Gateway mutation at a
    time: the helper serializes on its own mutex anyway, and a second login
    admitted beside a first is exactly a stacked 2FA push.
    """
    with _verb_history_guard:
        if _verbs_in_flight:
            busy = ", ".join(sorted(_verbs_in_flight))
            return f"busy: {busy} in progress; retry when it finishes"
        refusal = _cooldown_refusal_locked(verb, now)
        if refusal is not None:
            return refusal
        _verbs_in_flight.add(verb)
    return None


def finish_verb(verb: str, rc: int | None, *, record: str | None = None) -> None:
    """Release ``verb``'s claim and, in the same critical section, record the
    attempt unless the helper refused before acting (``rc`` in
    HELPER_NOT_ATTEMPTED_RCS). ``rc`` None means nothing ran. ``record``
    names the verb to record when it differs from the claim."""
    with _verb_history_guard:
        _verbs_in_flight.discard(verb)
        if rc is not None and rc not in HELPER_NOT_ATTEMPTED_RCS:
            _record_verb_locked(record or verb)


def login_throttle_refusal(verb: str, watchdog_state: str, now: float | None = None) -> str | None:
    """Reason a login verb is refused while IBKR throttles logins, else None."""
    if verb not in LOGIN_VERBS:
        return None
    retry_at, unreadable = login_throttle_gate(Path(watchdog_state))
    now = time.time() if now is None else now
    if retry_at is None or now >= retry_at:
        return None
    if unreadable:
        # Fail closed: the state may be hiding a live IBKR login throttle.
        return (
            "the watchdog state is unreadable, so an IBKR login throttle cannot be "
            f"ruled out; {verb} is refused until {format_utc(retry_at)} "
            "(check the watchdog, then restart once and approve the push)."
        )
    return (
        "IBKR is refusing Gateway logins (too many failed login attempts); "
        f"every {verb} is another failed login. Wait until {format_utc(retry_at)}, "
        "then restart once and approve the push."
    )


def record_verb(verb: str, now: float | None = None) -> None:
    with _verb_history_guard:
        _record_verb_locked(verb, now)


def helper_attempted(rc: int) -> bool:
    """True when a helper exit may have touched the Gateway."""
    return rc not in HELPER_NOT_ATTEMPTED_RCS


def _record_verb_locked(verb: str, now: float | None = None) -> None:
    _verb_history[verb] = time.monotonic() if now is None else now
    _verb_wall_history[verb] = time.time()
    _persist_verb_history(dict(_verb_wall_history))


def broker_lease(now: float | None = None) -> dict | None:
    """The 2FA push lease that would refuse a login, or None.

    Classified by the same ``ib_2fa_lock.classify_lease`` rule the helper's
    acquire honours, so the app never disarms an action the helper would
    accept. The orphan probe only runs for a lease past its grace window
    (bounded: ORPHAN_CONFIRM_PROBES connects) and outside the lease guard.
    """
    try:
        with ib_2fa_lock._guard(exclusive=False, timeout_secs=1.0):
            lock = ib_2fa_lock._read_lock_file()
    except Exception:  # noqa: BLE001 - status must never fail on the lease read
        return None
    now = time.time() if now is None else now
    if ib_2fa_lock.classify_lease(lock, now) is not ib_2fa_lock.LeaseState.HELD:
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
            self._ok(healthz_payload())
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
        if verb not in MUTATIONS and verb not in HOLD_VERBS and verb != ROTATE_VERB:
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
        body = self.rfile.read(length) if length else b""
        if verb in HOLD_VERBS:
            self._operator_hold(verb, body)
            return
        if verb == ROTATE_VERB:
            code, payload = self._rotate_password(body)
            self._ok(payload, code)
            return
        self._helper(verb)

    def _rotate_password(self, body: bytes) -> tuple[int, dict]:
        """Validate, gate, write, then restart once. Never echoes a value.

        Refuse-before-write for the short gates (a rotation in flight, the
        per-verb cooldown, a held 2FA lease): nothing changes on disk and the
        operator retries. Write-then-defer for an operator hold or an IBKR
        login throttle: the operator already changed the password on IBKR, so
        the old one is dead and leaving it in place makes the next sanctioned
        login a guaranteed failure.
        """
        cfg = self.server.gateway_config
        try:
            request = json.loads(body.decode("utf-8")) if body else None
        except (UnicodeDecodeError, ValueError):
            request = None
        if not isinstance(request, dict):
            return 400, {"ok": False, "code": "INVALID_VALUE", "detail": "body must be a JSON object"}
        unknown = sorted(str(key)[:40] for key in request if key not in ROTATE_FIELDS)
        if unknown:
            return 400, {"ok": False, "code": "INVALID_VALUE",
                         "detail": f"unsupported fields: {', '.join(unknown)}"}
        if "password" not in request:
            return 400, {"ok": False, "code": "INVALID_VALUE", "detail": "password: is required"}
        values = {"TWS_PASSWORD": request["password"]}
        if "userid" in request:
            values["TWS_USERID"] = request["userid"]
        for field, key in (("password", "TWS_PASSWORD"), ("userid", "TWS_USERID")):
            if key in values:
                reason = _gateway_value_error(values[key])
                if reason:
                    return 400, {"ok": False, "code": "INVALID_VALUE", "detail": f"{field}: {reason}"}
        actor = request.get("actor")
        if actor is not None and not isinstance(actor, str):
            return 400, {"ok": False, "code": "INVALID_VALUE", "detail": "actor: must be a string"}
        secrets = list(values.values())

        if not _rotate_guard.acquire(blocking=False):
            return 409, {"ok": False, "code": "ROTATION_IN_PROGRESS",
                         "detail": "a password rotation is already running"}
        refusal = admit_verb(ROTATE_VERB)
        if refusal is not None:
            _rotate_guard.release()
            return 409, {"ok": False, "code": "LOGIN_BLOCKED",
                         "reason": "busy" if refusal.startswith("busy") else "cooldown",
                         "detail": refusal}
        try:
            lease = broker_lease()
            if lease is not None:
                return 409, {"ok": False, "code": "LOGIN_BLOCKED", "reason": "lease",
                             "detail": (f"2FA push lease held by {lease['holder']}, "
                                        f"{lease['remaining_secs']}s left")}

            rc, output = run_credential_writer(cfg, values)
            if rc is None:
                return 502, {"ok": False, "written": None, "restarted": False,
                             "code": "WRITE_UNKNOWN", "detail": scrub(output, secrets)}
            if rc != 0:
                return 502, {"ok": False, "written": False, "restarted": False,
                             "code": "WRITE_FAILED", "returncode": rc,
                             "detail": scrub(output, secrets)}
            record_verb(ROTATE_VERB)

            deferred = None
            if ib_operator_hold.is_held():
                deferred = ("IBKR operator hold active; Resume Gateway in /admin "
                            "or `radon ib resume` on the broker logs in with the new password")
            else:
                deferred = login_throttle_refusal("restart", cfg["watchdog_state"])
            if deferred is not None:
                return 202, {"ok": True, "written": True, "restarted": False,
                             "code": "RESTART_DEFERRED", "detail": deferred}

            rc, detail = run_helper(cfg["helper"], "restart", cfg["timeout"])
            detail = scrub(detail, secrets)
            if helper_attempted(rc):
                # Recorded while the rotation still holds its claim: a failed
                # restart may already have sent a push.
                record_verb("restart")
            if rc == 0:
                return 200, {"ok": True, "written": True, "restarted": True,
                             "detail": detail or "Gateway restarted"}
            if rc in HELPER_DEFER_RCS:
                # The helper found a lease or a hold after the write. The old
                # password is dead: keep the new one, defer the restart.
                return 202, {"ok": True, "written": True, "restarted": False,
                             "code": "RESTART_DEFERRED", "returncode": rc,
                             "detail": detail or f"Gateway restart deferred (rc={rc})"}
            return 502, {"ok": False, "written": True, "restarted": False,
                         "code": "RESTART_FAILED", "returncode": rc, "detail": detail}
        finally:
            finish_verb(ROTATE_VERB, None)
            _rotate_guard.release()

    def _operator_hold(self, verb: str, body: bytes) -> None:
        cfg = self.server.gateway_config
        try:
            request = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, ValueError):
            self._refuse(400, "body is not JSON")
            return
        if not isinstance(request, dict):
            self._refuse(400, "body is not an object")
            return
        actor = "app:" + str(request.get("actor") or "unknown")[:HOLD_ACTOR_MAX]
        if verb == "hold":
            self._set_hold(cfg, request, actor)
        else:
            self._clear_hold(cfg, actor)

    def _set_hold(self, cfg: dict, request: dict, actor: str) -> None:
        reason = str(request.get("reason") or "operator hold from the admin panel")[:HOLD_REASON_MAX]
        expires_at = request.get("expires_at") or None
        hold_error = None
        try:
            ib_operator_hold.set_hold(reason, actor, expires_at)
        except (OSError, ValueError, TypeError) as exc:
            hold_error = str(exc)
        # Stop even when the flag failed: the operator is about to log in.
        # Never admission-gated: a hold must always be able to stop the Gateway.
        rc, detail = run_helper(cfg["helper"], "stop", cfg["timeout"])
        if helper_attempted(rc):
            record_verb("stop")
        payload = {
            "ok": hold_error is None and rc == 0,
            "verb": "hold",
            "operator_hold": ib_operator_hold.hold_state(),
            "gateway_stop": {"returncode": rc, "detail": detail},
        }
        if hold_error is not None:
            payload["detail"] = f"HOLD NOT WRITTEN ({hold_error}); run `radon ib release` on the broker"
        elif rc != 0:
            payload["detail"] = (
                f"hold set, but the Gateway stop failed (rc={rc}): {detail}. "
                "Run `radon ib release` on the broker"
            )
        else:
            payload["detail"] = "hold set; Gateway stopped. Wait ~30s, then log in to IBKR."
        self._ok(payload, 200 if payload["ok"] else 502)

    def _clear_hold(self, cfg: dict, actor: str) -> None:
        try:
            ib_operator_hold.clear_hold(actor)
        except OSError as exc:
            self._ok({"ok": False, "verb": "unhold", "detail": f"hold not cleared: {exc}",
                      "operator_hold": ib_operator_hold.hold_state()}, 502)
            return
        state = ib_operator_hold.hold_state()
        if state["held"]:
            self._ok({"ok": False, "verb": "unhold", "operator_hold": state,
                      "detail": "hold still reads held; run `radon ib resume` on the broker"}, 502)
            return
        # Recovery back on, never silently: one login now (one push), unless
        # the cooldown or an IBKR login throttle says a login would hurt. The
        # watchdog owns recovery from there.
        refusal = (
            cooldown_refusal("start")
            or login_throttle_refusal("start", cfg["watchdog_state"])
            or admit_verb("start")
        )
        if refusal is not None:
            start = {"ok": False, "returncode": CONTROL_BUSY_RC, "detail": refusal}
        else:
            rc = None
            try:
                rc, detail = run_helper(cfg["helper"], "start", cfg["timeout"])
            finally:
                finish_verb("start", rc)
            start = {"ok": rc == 0, "returncode": rc, "detail": detail}
        self._ok({
            "ok": True,
            "verb": "unhold",
            "operator_hold": state,
            "gateway_start": start,
            "detail": "hold cleared; approve the IBKR Mobile push" if start["ok"]
            else f"hold cleared; Gateway start deferred: {start['detail']}",
        })

    def _helper(self, verb: str) -> None:
        cfg = self.server.gateway_config
        # `radon ib release` holds the Gateway out of the IBKR username the
        # operator shares with it; a login here would kick the operator.
        if verb in LOGIN_VERBS and ib_operator_hold.is_held():
            self._ok({
                "ok": False,
                "verb": verb,
                "code": "OPERATOR_HOLD",
                "detail": (
                    "IBKR operator hold active; `radon ib resume` on the broker "
                    "or Resume Gateway in /admin clears it"
                ),
                "operator_hold": ib_operator_hold.hold_state(),
            }, 423)
            return
        if verb in MUTATIONS:
            # Cooldown first for its message; admit_verb then re-checks and
            # claims atomically.
            refusal = (
                cooldown_refusal(verb)
                or login_throttle_refusal(verb, cfg["watchdog_state"])
                or admit_verb(verb)
            )
            if refusal is not None:
                self._ok({"ok": False, "verb": verb, "returncode": CONTROL_BUSY_RC, "detail": refusal}, 409)
                return
            rc = None
            try:
                rc, detail = run_helper(cfg["helper"], verb, cfg["timeout"])
            finally:
                # Records any attempt, failed or timed out, before releasing.
                finish_verb(verb, rc)
        else:
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
                "operator_hold": ib_operator_hold.hold_state(),
            }
            self._ok(payload, 409 if rc in {LEASE_HELD_RC, CONTROL_BUSY_RC} else 200)
            return
        if rc in {LEASE_HELD_RC, CONTROL_BUSY_RC}:
            self._ok({"ok": False, "verb": verb, "returncode": rc, "detail": detail}, 409)
            return
        if rc == 0:
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
