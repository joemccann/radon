#!/usr/bin/env python3
"""Log-only egress proxy for the Mac runner.

The daemon runs as ``_radonegress``. ``_radonbot`` cannot change the
root-owned enable flag or the root-owned allowlist. This release always
forwards. A destination that is not allowlisted is recorded as
``would_block`` and still forwarded. ``mode block`` is ignored.
"""
from __future__ import annotations

import ipaddress
import json
import os
import plistlib
import re
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

EGRESS_USER = "_radonegress"
EGRESS_GROUP = "_radonegress"
RUNNER_USER = "_radonbot"
ENABLE_FLAG = Path("/usr/local/radon-runner/enable-egress")
ALLOWLIST_PATH = Path("/usr/local/radon-runner/egress-allowlist")
ANCHOR_PATH = Path("/etc/pf.anchors/com.radon.egress")
PF_CONF = Path("/etc/pf.conf")
PLIST_PATH = Path("/Library/LaunchDaemons/com.radon.egress-proxy.plist")
PREFIX = Path("/usr/local/radon-runner")
LOG_DIR = Path("/Library/Radon/egress/log")
DECISION_LOG = LOG_DIR / "decisions.log"
DAEMON_LOG = LOG_DIR / "daemon.log"
LISTEN_HOST = "localhost"
LISTEN_PORT = 8373
# One thread per tunnel. Past the cap a new client gets 503 and is closed;
# a tunnel with no traffic for this long is closed too.
MAX_CONNECTIONS = 64
TUNNEL_IDLE_SECS = 300
LABEL = "com.radon.egress-proxy"
_BEGIN = "# BEGIN com.radon.egress"
_END = "# END com.radon.egress"
_BLOCK = (
    "# BEGIN com.radon.egress\n"
    'anchor "com.radon.egress"\n'
    'load anchor "com.radon.egress" from "/etc/pf.anchors/com.radon.egress"\n'
    "# END com.radon.egress\n"
)
_HOST = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)"
    r"(?:\.(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?))*$"
)
_LOG_LOCK = threading.Lock()


@dataclass(frozen=True)
class Destination:
    kind: str
    value: str
    port: int | None


@dataclass(frozen=True)
class Allowlist:
    rules: tuple[Destination, ...] = ()
    ignored_mode: str | None = None

    def matches(self, host: str, port: int) -> bool:
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            ip = None
        host_key = None if ip is not None else _host_or_none(host)
        for rule in self.rules:
            if rule.port is not None and rule.port != port:
                continue
            if rule.kind == "host" and ip is None and host_key is not None and rule.value == host_key:
                return True
            if ip is not None and rule.kind == ("ipv4" if ip.version == 4 else "ipv6"):
                if ipaddress.ip_address(rule.value) == ip:
                    return True
        return False


def _host_or_none(host: str) -> str | None:
    try:
        return normalize_host(host)
    except ValueError:
        return None


def normalize_host(value: str) -> str:
    text = value.strip().rstrip(".").lower()
    try:
        text = text.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("host is not a hostname") from exc
    if not _HOST.fullmatch(text):
        raise ValueError("host is not a hostname")
    return text


def _port(token: str) -> int:
    if not token.isdigit():
        raise ValueError("port")
    value = int(token)
    if not 1 <= value <= 65535:
        raise ValueError("port")
    return value


def parse_allowlist(text: str) -> Allowlist:
    rules = []
    ignored = None
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] == "mode":
            if parts == ["mode", "log-only"]:
                continue
            if parts == ["mode", "block"]:
                ignored = "block"
                continue
            raise ValueError(f"line {lineno}: unknown mode")
        if parts[0] not in {"host", "ipv4", "ipv6"} or len(parts) not in {2, 3}:
            raise ValueError(f"line {lineno}: malformed rule")
        port = _port(parts[2]) if len(parts) == 3 else None
        kind = parts[0]
        if kind == "host":
            value = normalize_host(parts[1])
            try:
                ipaddress.ip_address(value)
            except ValueError:
                pass
            else:
                raise ValueError(f"line {lineno}: host line must be a name")
        elif kind == "ipv4":
            value = str(ipaddress.IPv4Address(parts[1]))
        else:
            value = str(ipaddress.IPv6Address(parts[1]))
        rules.append(Destination(kind, value, port))
    return Allowlist(tuple(rules), ignored)


def _bot_uid() -> int | None:
    try:
        import pwd
        return pwd.getpwnam(RUNNER_USER).pw_uid
    except (ImportError, KeyError):
        return None


def flag_verdict(info, parents, bot_uid=None) -> tuple[bool, str]:
    if not parents:
        return False, "no parent chain"
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return False, "not a regular file"
    if info.st_nlink != 1:
        return False, "hard link"
    if info.st_uid != 0:
        return False, "not root-owned"
    if bot_uid is not None and info.st_uid == bot_uid:
        return False, "owned by runner account"
    if info.st_mode & 0o022:
        return False, "group or world writable"
    for parent in parents:
        if stat.S_ISLNK(parent.st_mode) or not stat.S_ISDIR(parent.st_mode):
            return False, "parent is not a directory"
        if parent.st_uid != 0:
            return False, "parent not root-owned"
        if parent.st_mode & 0o022 and not parent.st_mode & stat.S_ISVTX:
            return False, "parent is writable"
    return True, "ok"


def flag_status(path: Path) -> tuple[bool, str]:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False, "missing"
    except OSError as exc:
        return False, f"unreadable {exc.errno}"
    if stat.S_ISLNK(info.st_mode):
        return False, "symlink"
    parents = []
    current = path.parent
    seen = set()
    while str(current) not in seen:
        seen.add(str(current))
        try:
            parents.append(current.lstat())
        except OSError:
            return False, "parent unreadable"
        if current.parent == current:
            break
        current = current.parent
    return flag_verdict(info, parents, bot_uid=_bot_uid())


def load_allowlist(path: Path) -> tuple[Allowlist, str]:
    ok, reason = flag_status(path)
    if reason == "missing":
        return Allowlist(), "allowlist_missing"
    if not ok:
        return Allowlist(), "allowlist_untrusted"
    try:
        parsed = parse_allowlist(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return Allowlist(), "allowlist_rejected"
    if parsed.ignored_mode:
        return parsed, "blocking_mode_ignored"
    return parsed, "ok"


def allowlist_install_ok(path: Path) -> tuple[bool, str]:
    ok, reason = flag_status(path)
    if reason == "missing":
        return True, reason
    return ok, reason


def render_anchor() -> str:
    lines = [
        "# com.radon.egress",
        "# Log-only. These pass rules do not drop, redirect, or override a later block.",
        "# Blocking mode is a later change and is not loaded.",
    ]
    for user in (RUNNER_USER, EGRESS_USER):
        for family in ("inet", "inet6"):
            lines.append(f"pass out log {family} proto {{ tcp, udp }} user {user}")
    return "\n".join(lines) + "\n"


def validate_anchor(text: str) -> None:
    active = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] != "pass" or "quick" in parts:
            raise ValueError("anchor is not log-only")
        active.append(line)
    if not active:
        raise ValueError("anchor is empty")
    blob = "\n".join(active)
    if RUNNER_USER not in blob or EGRESS_USER not in blob:
        raise ValueError("anchor does not name both accounts")


def merge_pf_conf(text: str) -> str:
    if text.count(_BEGIN) > 1 or text.count(_END) > 1:
        raise ValueError("pf.conf has repeated egress markers")
    if _BEGIN in text or _END in text:
        if _BEGIN not in text or _END not in text:
            raise ValueError("pf.conf has a partial egress marker")
        pre, rest = text.split(_BEGIN, 1)
        _middle, post = rest.split(_END, 1)
        if post.startswith("\n"):
            post = post[1:]
        merged = pre + _BLOCK + post
    else:
        if text and not text.endswith("\n"):
            text += "\n"
        merged = text + _BLOCK
    if not merged.endswith("\n"):
        merged += "\n"
    return merged


def load_pf_conf(path: Path, run) -> str:
    if not path.is_file():
        return "skipped-missing"
    original = path.read_text(encoding="utf-8")
    merged = merge_pf_conf(original)
    temporary = path.with_name(path.name + ".radon-egress.tmp")
    temporary.write_text(merged, encoding="utf-8")
    try:
        if run(["/sbin/pfctl", "-nf", str(temporary)]) != 0:
            raise RuntimeError("pf.conf syntax check failed")
        path.write_text(merged, encoding="utf-8")
        if run(["/sbin/pfctl", "-f", str(path)]) != 0:
            path.write_text(original, encoding="utf-8")
            run(["/sbin/pfctl", "-f", str(path)])
            raise RuntimeError("pf reload failed and the previous rules were restored")
    finally:
        temporary.unlink(missing_ok=True)
    return "loaded"


def account_create_commands(uid: int, gid: int, user_uuid: str, group_uuid: str, *, create_group: bool) -> list[list[str]]:
    commands = []
    if create_group:
        group = f"/Groups/{EGRESS_GROUP}"
        commands.extend([
            ["/usr/bin/dscl", ".", "-create", group],
            ["/usr/bin/dscl", ".", "-create", group, "GeneratedUID", group_uuid],
            ["/usr/bin/dscl", ".", "-create", group, "PrimaryGroupID", str(gid)],
            ["/usr/bin/dscl", ".", "-create", group, "Password", "*"],
        ])
    user = f"/Users/{EGRESS_USER}"
    commands.extend([
        ["/usr/bin/dscl", ".", "-create", user],
        ["/usr/bin/dscl", ".", "-create", user, "GeneratedUID", user_uuid],
        ["/usr/bin/dscl", ".", "-create", user, "UniqueID", str(uid)],
        ["/usr/bin/dscl", ".", "-create", user, "PrimaryGroupID", str(gid)],
        ["/usr/bin/dscl", ".", "-create", user, "NFSHomeDirectory", "/var/empty"],
        ["/usr/bin/dscl", ".", "-create", user, "UserShell", "/usr/bin/false"],
        ["/usr/bin/dscl", ".", "-create", user, "Password", "*"],
        ["/usr/bin/dscl", ".", "-create", user, "IsHidden", "1"],
    ])
    return commands


def pf_load_commands(anchor: Path, pf_conf: Path) -> list[list[str]]:
    return [
        ["/sbin/pfctl", "-nf", str(anchor)],
        ["/sbin/pfctl", "-a", "com.radon.egress", "-f", str(anchor)],
        ["/sbin/pfctl", "-nf", str(pf_conf)],
        ["/sbin/pfctl", "-f", str(pf_conf)],
    ]


def directory_commands() -> list[list[str]]:
    return [
        ["/usr/bin/install", "-d", "-o", "root", "-g", "wheel", "-m", "755", "/Library/Radon", "/Library/Radon/egress"],
        ["/usr/bin/install", "-d", "-o", EGRESS_USER, "-g", EGRESS_GROUP, "-m", "750", str(LOG_DIR)],
    ]


def launch_commands(plist: Path) -> list[list[str]]:
    return [
        ["/bin/launchctl", "bootout", f"system/{LABEL}"],
        ["/bin/launchctl", "bootstrap", "system", str(plist)],
    ]


def render_plist(python: str, script: str) -> bytes:
    payload = {
        "Label": LABEL,
        "UserName": EGRESS_USER,
        "ProgramArguments": [python, "-I", script, "serve"],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ProcessType": "Background",
        "StandardOutPath": str(DAEMON_LOG),
        "StandardErrorPath": str(DAEMON_LOG),
        "EnvironmentVariables": {
            "HOME": "/var/empty",
            "PATH": "/usr/bin:/bin",
            "DISABLE_AUTOUPDATER": "1",
        },
    }
    return plistlib.dumps(payload, fmt=plistlib.FMT_XML)


def parse_dscl_user(text: str) -> dict:
    fields = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return {
        "shell": fields.get("UserShell", ""),
        "home": fields.get("NFSHomeDirectory", ""),
        "uid": fields.get("UniqueID", ""),
        "gid": fields.get("PrimaryGroupID", ""),
        "hidden": fields.get("IsHidden", ""),
    }


def admin_membership_is_safe(text: str) -> bool:
    return "is not a member" in text


def account_is_safe(record: dict) -> bool:
    try:
        uid = int(record["uid"])
        bot_uid = int(record["bot_uid"])
    except (KeyError, TypeError, ValueError):
        return False
    return bool(
        record.get("shell") == "/usr/bin/false"
        and record.get("home") == "/var/empty"
        and str(record.get("hidden")) == "1"
        and uid > 0
        and uid != bot_uid
        and admin_membership_is_safe(str(record.get("admin_text", "")))
    )


def _run(argv, *, check=True):
    result = subprocess.run(argv, capture_output=True, text=True)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"{Path(argv[0]).name} failed ({result.returncode}): {detail}")
    return result


def _command_ok(argv) -> bool:
    return _run(argv, check=False).returncode == 0


def _reserved_id(search_path: str, key: str) -> int:
    for number in range(400, 500):
        result = _run(["/usr/bin/dscl", ".", "-search", search_path, key, str(number)], check=False)
        if result.returncode == 0 and not result.stdout.strip():
            return number
    raise RuntimeError(f"no reserved id in {search_path}")


def _account_record() -> dict:
    text = _run(["/usr/bin/dscl", ".", "-read", f"/Users/{EGRESS_USER}"]).stdout
    admin = _run(["/usr/bin/dsmemberutil", "checkmembership", "-U", EGRESS_USER, "-G", "admin"], check=False)
    bot = _run(["/usr/bin/id", "-u", RUNNER_USER], check=False)
    record = parse_dscl_user(text)
    record["admin_text"] = admin.stdout or ""
    record["bot_uid"] = int(bot.stdout.strip()) if bot.returncode == 0 and bot.stdout.strip().isdigit() else -1
    return record


def ensure_account() -> None:
    if _command_ok(["/usr/bin/id", EGRESS_USER]):
        if not account_is_safe(_account_record()):
            raise RuntimeError("egress account exists and is not safe")
        return
    group = f"/Groups/{EGRESS_GROUP}"
    create_group = not _command_ok(["/usr/bin/dscl", ".", "-read", group])
    if create_group:
        gid = _reserved_id("/Groups", "PrimaryGroupID")
    else:
        gid = int(_run(["/usr/bin/dscl", ".", "-read", group, "PrimaryGroupID"]).stdout.split(":", 1)[1].strip())
    commands = account_create_commands(
        _reserved_id("/Users", "UniqueID"), gid, str(uuid.uuid4()), str(uuid.uuid4()), create_group=create_group,
    )
    for command in commands:
        _run(command)
    if not account_is_safe(_account_record()):
        raise RuntimeError("egress account failed verification")


def _python_bin() -> str:
    configured = os.environ.get("RADON_EGRESS_PYTHON", "/opt/homebrew/bin/python3.13")
    if not configured.startswith("/") or ".." in configured.split("/"):
        raise RuntimeError("egress python must be an absolute path")
    return configured


def _write_bytes(path: Path, payload: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    if os.geteuid() == 0:
        os.chown(temporary, 0, 0)
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def apply_install() -> None:
    ensure_account()
    for command in directory_commands():
        _run(command)
    _write_bytes(PLIST_PATH, render_plist(_python_bin(), str(PREFIX / "egress_proxy.py")), 0o644)
    anchor = render_anchor()
    validate_anchor(anchor)
    ANCHOR_PATH.parent.mkdir(parents=True, exist_ok=True)
    _write_bytes(ANCHOR_PATH, anchor.encode("utf-8"), 0o644)
    for command in pf_load_commands(ANCHOR_PATH, PF_CONF)[:2]:
        _run(command)
    load_pf_conf(PF_CONF, lambda argv: subprocess.run(argv, capture_output=True, text=True).returncode)
    for command in launch_commands(PLIST_PATH):
        _run(command, check=command[1] != "bootout")


def cmd_install() -> int:
    enabled, reason = flag_status(ENABLE_FLAG)
    if not enabled:
        print(f"egress: disabled ({reason})")
        return 0
    ok, why = allowlist_install_ok(ALLOWLIST_PATH)
    if not ok:
        print(f"egress: allowlist refused ({why})", file=sys.stderr)
        return 1
    if sys.platform != "darwin":
        print("egress: refusing to install off macOS", file=sys.stderr)
        return 1
    apply_install()
    print("egress: log-only")
    return 0


def cmd_plan() -> int:
    enabled, reason = flag_status(ENABLE_FLAG)
    if not enabled:
        print(f"egress: disabled ({reason})")
        return 0
    print("egress: enabled")
    return 0


def _event(decision: str, action: str, host: str, port: int, reason: str) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "ts": now.isoformat(),
        "day": now.date().isoformat(),
        "decision": decision,
        "action": action,
        "host": host,
        "port": port,
        "reason": reason,
    }


def append_event(path: Path, event: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(event, separators=(",", ":")) + "\n").encode("utf-8")
    with _LOG_LOCK:
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o640)
        try:
            os.write(fd, line)
        finally:
            os.close(fd)


def _reply(conn: socket.socket, code: int, reason: str) -> None:
    if code == 200:
        payload = b"HTTP/1.1 200 Connection Established\r\n\r\n"
    else:
        payload = f"HTTP/1.1 {code} {reason}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode()
    conn.sendall(payload)


def _split_connect(target: str) -> tuple[str, int]:
    if target.startswith("["):
        host, sep, port_s = target[1:].partition("]")
        if sep != "]" or not port_s.startswith(":"):
            raise ValueError("connect target")
        return host, _port(port_s[1:])
    host, sep, port_s = target.rpartition(":")
    if not sep or ":" in host:
        raise ValueError("connect target")
    return host, _port(port_s)


def classify(allowlist: Allowlist, host: str, port: int, load_reason: str) -> tuple[str, str]:
    if load_reason not in {"ok", "blocking_mode_ignored"}:
        return "would_block", load_reason
    if allowlist.matches(host, port):
        return "allow", "allowlisted"
    return "would_block", "not_allowlisted"


def _loop_names() -> set[str]:
    names = {"localhost", "::1"}
    try:
        for item in socket.getaddrinfo("localhost", None):
            names.add(str(item[4][0]).lower())
    except OSError:
        pass
    return names


def _is_loop(host: str, port: int) -> bool:
    return port == LISTEN_PORT and host.lower().strip("[]") in _loop_names()


def relay(left: socket.socket, right: socket.socket, idle) -> None:
    left.setblocking(False)
    right.setblocking(False)
    pending = {left: right, right: left}
    while pending:
        readable, _, _ = select.select(list(pending), [], [], idle)
        if not readable:
            return
        for src in readable:
            dst = pending.get(src)
            if dst is None:
                continue
            try:
                data = src.recv(65536)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                data = b""
            if not data:
                pending.pop(src, None)
                try:
                    dst.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            try:
                dst.setblocking(True)
                dst.sendall(data)
            except OSError:
                return
            finally:
                try:
                    dst.setblocking(False)
                except OSError:
                    return


def resolve_system(host: str, port: int):
    found = []
    for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        sockaddr = item[4]
        found.append((sockaddr[0], sockaddr[1]))
    return found


def handle_connection(conn, allowlist, log_path, resolve, load_reason="ok", idle=None) -> None:
    try:
        if not peer_allowed(conn):
            append_event(log_path, _event("refuse", "close", "", 0, "peer"))
            _reply(conn, 403, "Forbidden")
            return
        conn.settimeout(10)
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(1024)
            if not chunk:
                return
            data += chunk
            if len(data) > 8192:
                _reply(conn, 400, "Bad Request")
                return
        head, _, rest = data.partition(b"\r\n\r\n")
        try:
            line = head.decode("ascii").split("\r\n", 1)[0]
            method, target, version = line.split(" ")
        except (UnicodeError, ValueError):
            _reply(conn, 400, "Bad Request")
            return
        if method.upper() != "CONNECT" or not version.startswith("HTTP/1."):
            append_event(log_path, _event("unsupported", "refuse", "", 0, "method"))
            _reply(conn, 405, "Method Not Allowed")
            return
        try:
            host, port = _split_connect(target)
        except ValueError:
            _reply(conn, 400, "Bad Request")
            return
        decision, reason = classify(allowlist, host, port, load_reason)
        if _is_loop(host, port):
            append_event(log_path, _event("would_block", "refuse", host, port, "proxy_loop"))
            _reply(conn, 502, "Bad Gateway")
            return
        append_event(log_path, _event(decision, "forward", host, port, reason))
        upstream = None
        try:
            addresses = resolve(host, port)
        except OSError:
            addresses = []
        for address, dest_port in addresses:
            sock = socket.socket()
            sock.settimeout(10)
            try:
                sock.connect((address, dest_port))
            except OSError:
                sock.close()
                continue
            upstream = sock
            break
        if upstream is None:
            _reply(conn, 502, "Bad Gateway")
            return
        _reply(conn, 200, "Connection Established")
        if rest:
            upstream.sendall(rest)
        relay(conn, upstream, idle)
        upstream.close()
    except OSError:
        return
    finally:
        try:
            conn.close()
        except OSError:
            pass


def cmd_probe() -> int:
    try:
        with socket.create_connection((LISTEN_HOST, LISTEN_PORT), 0.5):
            return 0
    except OSError:
        return 1


def peer_uid(conn: socket.socket) -> int | None:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        return None
    try:
        raw = conn.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw[:struct.calcsize("3i")])
    except (OSError, struct.error):
        return None
    if uid < 0:
        return None
    return uid


def peer_allowed(conn: socket.socket) -> bool:
    # SO_PEERCRED exists on Linux only. On Darwin TCP there is no peer
    # identity for a loopback connection, so uid is None and every local
    # account passes: the proxy is a log-only observer, not an authenticator.
    uid = peer_uid(conn)
    if uid is None or uid <= 0 or uid == os.geteuid():
        return True
    bot = _bot_uid()
    return bot is not None and uid == bot


def cmd_serve() -> int:
    stop = threading.Event()

    def _stop(_signum, _frame):
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((LISTEN_HOST, LISTEN_PORT))
    server.listen(64)
    server.settimeout(0.5)
    try:
        serve_loop(server, stop, threading.BoundedSemaphore(MAX_CONNECTIONS))
    finally:
        server.close()
    return 0


def serve_loop(server: socket.socket, stop: threading.Event, slots: threading.BoundedSemaphore) -> None:
    def _run(conn, allow, reason):
        try:
            handle_connection(conn, allow, DECISION_LOG, resolve_system, load_reason=reason, idle=TUNNEL_IDLE_SECS)
        finally:
            slots.release()

    while not stop.is_set():
        try:
            conn, _addr = server.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        if not slots.acquire(blocking=False):
            try:
                append_event(DECISION_LOG, _event("refuse", "close", "", 0, "busy"))
                _reply(conn, 503, "Service Unavailable")
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass
            continue
        allow, reason = load_allowlist(ALLOWLIST_PATH)
        threading.Thread(target=_run, args=(conn, allow, reason), daemon=True).start()


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: egress_proxy.py gate|plan|install|serve|probe", file=sys.stderr)
        return 64
    command = args[0]
    if command == "gate":
        return 0 if flag_status(ENABLE_FLAG)[0] else 1
    if command == "plan":
        return cmd_plan()
    if command == "install":
        return cmd_install()
    if command == "serve":
        return cmd_serve()
    if command == "probe":
        return cmd_probe()
    print("usage: egress_proxy.py gate|plan|install|serve|probe", file=sys.stderr)
    return 64


if __name__ == "__main__":
    raise SystemExit(main())
