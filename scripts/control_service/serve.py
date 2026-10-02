"""Host control daemon for the /admin service controls (radon-control.service).

radon-api runs in a container with every capability dropped, no-new-privileges
and no systemctl, so it cannot control units itself. This daemon runs on the
host as ``radon`` and is the only bridge:

* Transport: a unix socket in ``/run/radon-control`` (systemd
  ``RuntimeDirectory``, mode 0700, socket 0600, owner radon). No TCP port.
  ``radon-app-runtime`` bind-mounts that directory into the radon-api
  container only; no other container can reach it.
* Peer check: ``SO_PEERCRED`` must report this daemon's own uid (radon) or
  root. Every such principal already holds the same sudoers grant, so the
  socket hands nobody a capability they lack.
* Allowlist: units must be in the live ``systemctl list-units 'radon-*'``
  registry, match the operator CLI's own ``radon-NAME.{service,timer}`` rule
  and the API's ``is_valid_unit``, and not be Gateway, beta or this daemon.
  Verbs are start|stop|restart, plus a full-stack restart.
* Execution: exactly ``sudo -n /usr/local/bin/radon unit <verb> <unit>`` or
  ``sudo -n /usr/local/bin/radon restart`` (sudoers.d/radon-ops), argv list,
  never a shell. The operator CLI takes the deploy lock itself.
* Self-protection: radon-api and radon-nextjs host the panel, so Stop is
  refused for them and Restart (like the full-stack restart) is answered
  first and run detached after a short delay. This daemon refuses to act on
  itself, and ``radon stop|restart`` excludes it.
* Audit: one ``radon-control audit {json}`` journald line per request with
  the actor the API forwarded, the peer uid, unit, verb and result.

Wire format: one JSON object per line each way (``api.host_control``).
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import socket
import socketserver
import stat
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Optional

try:
    from api import host_control
    from api import services as registry
except ImportError:  # `python -m scripts.control_service.serve` from the repo root
    from scripts.api import host_control
    from scripts.api import services as registry

CONTROL_UNIT = "radon-control.service"
SUDO_PATH = "/usr/bin/sudo"
OPERATOR_PATH = registry.OPERATOR_CLI_PATH
VERBS = ("start", "stop", "restart")
# Mirrors operator-radon.sh's own `unit` argument check, which is stricter
# than the API allowlist (the suffix is mandatory there).
UNIT_RE = re.compile(r"^radon-[a-z0-9-]+\.(?:service|timer)$")
REFUSED_UNITS = frozenset({
    CONTROL_UNIT,
    registry.GATEWAY_UNIT,
    registry.GATEWAY_PREHELD_UNIT,
    "radon-ib-gateway-remote.service",
})
# Units that serve the admin panel itself. Stopping one strands the operator
# with no panel to start it again; restarting one drops the HTTP request that
# asked for it, so the reply must go out first.
PANEL_HOST_UNITS = frozenset({"radon-api.service", "radon-nextjs.service"})
STATUS_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "Description",
    "Type",
    "ActiveEnterTimestamp",
    "InactiveEnterTimestamp",
    "ExecMainStartTimestamp",
    "ExecMainExitTimestamp",
    "ExecMainStatus",
    "LastTriggerUSec",
)
OPS = frozenset({"ping", "status", "unit", "stack-restart"})
_OP_KEYS = {
    "ping": frozenset({"op"}),
    "status": frozenset({"op"}),
    "unit": frozenset({"op", "verb", "unit", "actor"}),
    "stack-restart": frozenset({"op", "actor"}),
}
MAX_REQUEST_BYTES = 4096
CONN_TIMEOUT_S = 5.0
QUERY_TIMEOUT_S = 10.0
# The operator CLI's own budgets are 40s (unit) and 165s (stack).
UNIT_TIMEOUT_S = 60.0
STACK_TIMEOUT_S = 180.0
DETACH_DELAY_S = 1.0
STATUS_CACHE_S = 3.0
# operator-radon.sh exits 74 when the deploy/control lock is held.
LOCK_HELD_RC = 74
TIMEOUT_RC = 124
_ACTOR_RE = re.compile(r"^[A-Za-z0-9_.@:-]{1,128}$")
_ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}


def audit(event: dict) -> None:
    """One journald line per request. Never raises."""
    try:
        sys.stderr.write("radon-control audit " + json.dumps(event, sort_keys=True) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def sanitize_actor(value: object) -> str:
    if value is None or value == "":
        return "unknown"
    if isinstance(value, str) and _ACTOR_RE.match(value):
        return value
    return "invalid"


def allowed_actions(unit: str) -> list[str]:
    """Verbs the panel may run on ``unit`` through this daemon."""
    if not UNIT_RE.match(unit or "") or unit in REFUSED_UNITS or unit.startswith("radon-beta-"):
        return []
    if not registry.is_valid_unit(unit):
        return []
    if unit in PANEL_HOST_UNITS:
        return ["restart"]
    return list(VERBS)


def parse_request(raw: bytes) -> dict:
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("request too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("request is not JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("request is not an object")
    return payload


def run_command(argv: list[str], timeout: float) -> tuple[int, str]:
    """Exec a fixed argv (no shell) and return (returncode, detail)."""
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            shell=False,
            env=dict(_ENV),
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return TIMEOUT_RC, f"timed out after {timeout:.0f}s"
    except OSError as exc:
        return 127, f"exec failed: {exc}"
    detail = (proc.stderr or "").strip() or (proc.stdout or "").strip()
    return (proc.returncode if proc.returncode is not None else 1), detail[-2000:]


def parse_show_blocks(raw: str) -> list[dict]:
    """``systemctl show -p ... a b c`` output -> one dict per unit."""
    rows: list[dict] = []
    current: dict = {}
    for line in raw.splitlines():
        if not line.strip():
            if current:
                rows.append(current)
                current = {}
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        current[key.strip()] = value.strip()
    if current:
        rows.append(current)
    return rows


def _systemctl_query(*args: str) -> str:
    proc = subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        timeout=QUERY_TIMEOUT_S,
        check=False,
        shell=False,
        env=dict(_ENV),
        stdin=subprocess.DEVNULL,
    )
    if proc.returncode != 0:
        raise OSError(f"systemctl {args[0]} exited {proc.returncode}")
    return proc.stdout


def systemd_loaded_units() -> list[str]:
    raw = _systemctl_query("list-units", "radon-*", "--all", "--no-legend", "--plain", "--no-pager")
    units = []
    for line in raw.splitlines():
        parts = line.split()
        if parts and UNIT_RE.match(parts[0]):
            units.append(parts[0])
    return units


def systemd_unit_rows(units: list[str]) -> list[dict]:
    if not units:
        return []
    args = ["show", "--no-pager"]
    for prop in STATUS_PROPERTIES:
        args += ["-p", prop]
    return parse_show_blocks(_systemctl_query(*args, "--", *units))


def deploy_lock_held(path: Path) -> bool:
    """Non-blocking probe of the deploy/control lock. Missing file = free."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return False
    finally:
        os.close(fd)  # closing releases a lock we just took
    return False


def _spawn_thread(fn: Callable[[], None]) -> None:
    threading.Thread(target=fn, name="radon-control-detached", daemon=False).start()


class Controller:
    """Request dispatcher. Every collaborator is injectable for tests."""

    def __init__(
        self,
        *,
        runner: Callable[[list[str], float], tuple[int, str]] = run_command,
        loaded_units: Optional[Callable[[], list[str]]] = None,
        unit_rows: Optional[Callable[[], list[dict]]] = None,
        deploy_lock_path: Optional[Path] = None,
        spawn: Callable[[Callable[[], None]], None] = _spawn_thread,
        detach_delay: float = DETACH_DELAY_S,
    ) -> None:
        self.runner = runner
        self.loaded_units = loaded_units or systemd_loaded_units
        self.unit_rows = unit_rows or (lambda: systemd_unit_rows(self.loaded_units()))
        self.deploy_lock_path = Path(deploy_lock_path or registry.DEPLOY_LOCK_FILE)
        self.spawn = spawn
        self.detach_delay = detach_delay
        self._detached_guard = threading.Lock()
        self._detached_pending = False
        self._status_lock = threading.Lock()
        self._status_cache: tuple[float, list[dict]] = (0.0, [])
        self._uid = os.getuid()

    # --- entry point -----------------------------------------------------
    def handle(self, request: dict, peer_uid: Optional[int]) -> dict:
        op = request.get("op") if isinstance(request, dict) else None
        actor = sanitize_actor(request.get("actor")) if isinstance(request, dict) else "unknown"
        event = {
            "actor": actor,
            "peer_uid": peer_uid,
            "op": op if isinstance(op, str) and op in OPS else "invalid",
            "unit": request.get("unit") if isinstance(request.get("unit"), str) else None,
            "verb": request.get("verb") if isinstance(request.get("verb"), str) else None,
        }
        reply = self._dispatch(request, peer_uid, op)
        if event["op"] in {"unit", "stack-restart", "invalid"} or not reply.get("ok"):
            if reply.get("refused"):
                result = "refused"
            elif reply.get("accepted"):
                result = "accepted"
            else:
                result = "ok" if reply.get("ok") else "failed"
            audit({**event, "result": result, "returncode": reply.get("returncode"),
                   "detail": str(reply.get("detail") or reply.get("error") or "")[:300]})
        return reply

    def _dispatch(self, request: dict, peer_uid: Optional[int], op: object) -> dict:
        if peer_uid is None or peer_uid not in {self._uid, 0}:
            return {"ok": False, "refused": True, "error": "peer not allowed", "returncode": -1}
        if not isinstance(op, str) or op not in OPS:
            return _refuse("unknown op")
        extra = set(request) - _OP_KEYS[op]
        if extra:
            return _refuse(f"unexpected fields: {sorted(extra)}")
        if op == "ping":
            return {"ok": True, "service": "radon-control", "version": 1}
        if op == "status":
            return self._status()
        if op == "stack-restart":
            return self._detached(
                [SUDO_PATH, "-n", OPERATOR_PATH, "restart"], STACK_TIMEOUT_S,
                "full stack restart",
            )
        return self._unit(request)

    # --- ops ---------------------------------------------------------------
    def _status(self) -> dict:
        now = time.monotonic()
        with self._status_lock:
            cached_at, cached = self._status_cache
            if cached_at and now - cached_at < STATUS_CACHE_S:
                return {"ok": True, "units": cached}
        try:
            rows = self.unit_rows()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            return {"ok": False, "error": f"status unavailable: {exc}"}
        out = []
        for row in rows:
            unit = row.get("Id", "")
            if not UNIT_RE.match(unit):
                continue
            out.append({**row, "allowed_actions": allowed_actions(unit)})
        with self._status_lock:
            self._status_cache = (now, out)
        return {"ok": True, "units": out}

    def _unit(self, request: dict) -> dict:
        verb = request.get("verb")
        unit = request.get("unit")
        if not isinstance(verb, str) or verb not in VERBS:
            return _refuse("verb not allowed")
        if not isinstance(unit, str) or not UNIT_RE.match(unit):
            return _refuse("unit not allowed")
        if verb not in allowed_actions(unit):
            if unit in PANEL_HOST_UNITS:
                return _refuse(f"{verb} {unit} would take down the admin panel; use the operator CLI over ssh")
            return _refuse(f"{unit} is not controllable from the panel")
        try:
            loaded = set(self.loaded_units())
        except (OSError, subprocess.SubprocessError) as exc:
            return {"ok": False, "error": f"unit registry unavailable: {exc}", "returncode": -1}
        if unit not in loaded:
            return _refuse(f"{unit} is not a loaded radon unit")
        argv = [SUDO_PATH, "-n", OPERATOR_PATH, "unit", verb, unit]
        if unit in PANEL_HOST_UNITS:
            return self._detached(argv, UNIT_TIMEOUT_S, f"{verb} {unit}")
        rc, detail = self.runner(argv, UNIT_TIMEOUT_S)
        self._invalidate_status()
        return {"ok": rc == 0, "returncode": rc, "detail": detail or f"{verb} exited {rc}"}

    def _detached(self, argv: list[str], timeout: float, label: str) -> dict:
        if deploy_lock_held(self.deploy_lock_path):
            return {"ok": False, "returncode": LOCK_HELD_RC, "detail": "deploy/control lock held"}
        with self._detached_guard:
            if self._detached_pending:
                return {"ok": False, "returncode": LOCK_HELD_RC,
                        "detail": "another panel-host action is already scheduled"}
            self._detached_pending = True

        def run() -> None:
            try:
                time.sleep(self.detach_delay)
                rc, detail = self.runner(argv, timeout)
                audit({"op": "detached", "label": label, "argv": argv[3:],
                       "result": "ok" if rc == 0 else "failed", "returncode": rc,
                       "detail": detail[:300]})
            finally:
                with self._detached_guard:
                    self._detached_pending = False
                self._invalidate_status()

        self.spawn(run)
        return {"ok": True, "accepted": True, "returncode": 0,
                "detail": f"{label} accepted; it runs in {self.detach_delay:.0f}s and the panel reconnects"}

    def _invalidate_status(self) -> None:
        with self._status_lock:
            self._status_cache = (0.0, [])


def _refuse(reason: str) -> dict:
    return {"ok": False, "refused": True, "error": reason, "detail": reason, "returncode": -1}


# --- socket server -----------------------------------------------------------

def peer_uid(sock: socket.socket) -> Optional[int]:
    """The connecting process's uid via SO_PEERCRED (Linux). None if unknown."""
    opt = getattr(socket, "SO_PEERCRED", None)
    if opt is None:
        return None
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, opt, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
    except OSError:
        return None
    return uid


class _Handler(socketserver.StreamRequestHandler):
    timeout = CONN_TIMEOUT_S

    def handle(self) -> None:
        controller: Controller = self.server.controller  # type: ignore[attr-defined]
        try:
            raw = self.rfile.readline(MAX_REQUEST_BYTES + 1)
        except OSError:
            return
        uid = peer_uid(self.connection)
        try:
            request = parse_request(raw.strip())
        except ValueError as exc:
            reply = _refuse(str(exc))
            audit({"op": "invalid", "peer_uid": uid, "result": "refused", "detail": str(exc)})
        else:
            reply = controller.handle(request, uid)
        try:
            self.wfile.write((json.dumps(reply, separators=(",", ":")) + "\n").encode("utf-8"))
        except OSError:
            pass


class _Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str, controller: Controller) -> None:
        self.controller = controller
        super().__init__(path, _Handler)


def make_server(path: str, controller: Controller) -> _Server:
    """Bind the owner-only socket, replacing a stale socket but nothing else."""
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        pass
    else:
        if not stat.S_ISSOCK(info.st_mode):
            raise OSError(f"refusing to replace non-socket at {path}")
        os.unlink(path)
    old = os.umask(0o177)
    try:
        server = _Server(path, controller)
    finally:
        os.umask(old)
    os.chmod(path, 0o600)
    return server


def main() -> int:
    path = host_control.socket_path()
    server = make_server(path, Controller())
    audit({"op": "start", "socket": path, "uid": os.getuid(), "result": "listening"})
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
