#!/usr/bin/python3 -I
"""Root-owned writer for the IB Gateway credential file.

Installed root:root 0755 at /usr/local/sbin/radon-ib-gateway-credential by the
broker control plane. The IB Gateway container reads ONLY
/etc/radon/ib-gateway/gateway.env (radon-docker-gw pins it as the compose
env_file), so it no longer inherits every app secret in /etc/radon/env.

Verbs:
  write           radon may run this one, exactly, via `sudo -n` (sudoers.d/
                  radon-ops). Reads {"TWS_PASSWORD": ..., "TWS_USERID"?: ...}
                  as JSON on stdin and replaces only those lines, atomically,
                  keeping a root-only copy of the previous file for rollback.
  migrate         root only. Builds gateway.env from /etc/radon/env, copying
                  only GATEWAY_ENV_KEYS. Idempotent: TWS_PASSWORD / TWS_USERID
                  come from an existing gateway.env first, so a re-run never
                  reverts a rotation. Re-run after changing TRADING_MODE.
  prune-root-env  root only. Removes the TWS_PASSWORD / TWS_USERID lines from
                  /etc/radon/env once gateway.env carries them.
  rollback        root only. Restores gateway.env from gateway.env.prev.

Nothing here prints a value: output carries key names and paths only.
Exit codes: 0 ok, 64 usage, 65 invalid input, 74 busy, 77 not root,
78 untrusted or missing target.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import re
import stat
import sys
import tempfile

EXIT_USAGE = 64
EXIT_INVALID = 65
EXIT_BUSY = 74
EXIT_NOT_ROOT = 77
EXIT_UNTRUSTED = 78

MAX_STDIN_BYTES = 4096
MAX_VALUE_CHARS = 256
CREDENTIAL_KEYS = ("TWS_USERID", "TWS_PASSWORD")

# The only names the Gateway container receives from a host file. Everything
# the gnzsnz/ib-gateway image and IBC read; nothing an app service reads.
# Pinned by cloud/tests/test_ib_gateway_credential.py::test_allowlist_pinned.
GATEWAY_ENV_KEYS = frozenset({
    # Compose + cloud/.env.example (the Gateway block).
    "TWS_USERID",
    "TWS_PASSWORD",
    "TRADING_MODE",
    "READ_ONLY_API",
    "TWS_ACCEPT_INCOMING",
    "VNC_SERVER_PASSWORD",
    # Pinned in the compose `environment:` block (it wins over env_file);
    # copied so the container env is complete if that pin is ever dropped.
    "EXISTING_SESSION_DETECTED_ACTION",
    "TWOFA_TIMEOUT_ACTION",
    "RELOGIN_AFTER_TWOFA_TIMEOUT",
    "AUTO_RESTART_TIME",
    "TWS_COLD_RESTART",
    # Optional image knobs, copied only when present on the host.
    "TIME_ZONE",
    "TZ",
    "JAVA_HEAP_SIZE",
    "TWOFA_DEVICE",
    "BYPASS_WARNING",
    "AUTO_LOGOFF_TIME",
    "SAVE_TWS_SETTINGS",
    "TWS_MASTER_CLIENT_ID",
    "ALLOW_BLIND_TRADING",
})
# Present on the host but not allowlisted: worth a warning, never a copy.
_REVIEW_PREFIX = re.compile(r"^(TWS_|IBC_|VNC_|TWOFA_|JAVA_)")
_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_HEADER = (
    "# Managed by /usr/local/sbin/radon-ib-gateway-credential. Root-only.\n"
    "# The IB Gateway container's env_file. Re-run `migrate` after changing a\n"
    "# Gateway setting in /etc/radon/env; the web app rotates TWS_PASSWORD.\n"
)


class WriterError(Exception):
    """A refusal. The message names keys and paths, never a value."""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def _test_mode() -> bool:
    # Test overrides are honoured only for a non-root caller. sudo's env_reset
    # strips them anyway; this makes the root path immune even without it.
    return os.geteuid() != 0 and bool(os.environ.get("RADON_TEST_GATEWAY_DIR"))


def _paths() -> dict:
    if _test_mode():
        gateway_dir = os.environ["RADON_TEST_GATEWAY_DIR"]
        root_env = os.environ.get("RADON_TEST_ROOT_ENV") or os.path.join(gateway_dir, "env")
    else:
        gateway_dir = "/etc/radon/ib-gateway"
        root_env = "/etc/radon/env"
    return {
        "dir": gateway_dir,
        "target": os.path.join(gateway_dir, "gateway.env"),
        "prev": os.path.join(gateway_dir, "gateway.env.prev"),
        "lock": os.path.join(gateway_dir, ".lock"),
        "root_env": root_env,
        "root_env_backup": os.path.join(os.path.dirname(root_env), "env.pre-ib-gateway-split"),
    }


def _owner() -> tuple[int, int]:
    return (os.getuid(), os.getgid()) if _test_mode() else (0, 0)


def value_error(value: object) -> str | None:
    """Why a value cannot be written as KEY='value', else None.

    Same rule in scripts/api/routes/credentials.py and
    scripts/ib_gateway_remote/serve.py (shared vectors in
    scripts/tests/test_ib_gateway_remote_rotate.py). Inside single quotes
    docker compose keeps $ # " \\ and spaces literal; a quote, a control byte
    or a trailing backslash would change or break the parse.
    """
    if not isinstance(value, str):
        return "must be a string"
    if not value:
        return "must not be empty"
    if len(value) > MAX_VALUE_CHARS:
        return f"must be at most {MAX_VALUE_CHARS} characters"
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


def _split_lines(text: str) -> list:
    """Lines with their \n kept, split on \n only.

    docker compose and systemd read an env file line by line on \n.
    str.splitlines() also breaks on U+2028, U+2029, U+0085 and \x1c-\x1e,
    so it would cut one assignment in two and surface its tail as a key.
    """
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _render(key: str, value: str) -> str:
    return f"{key}=\n" if value == "" else f"{key}='{value}'\n"


def _require_root() -> None:
    if os.geteuid() != 0 and not _test_mode():
        raise WriterError("radon-ib-gateway-credential must run as root", EXIT_NOT_ROOT)


def _trusted_dir(path: str) -> None:
    uid, _ = _owner()
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise WriterError(f"{path} is missing; run radon-ib-gateway-credential migrate", EXIT_UNTRUSTED)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != uid or st.st_mode & 0o077:
        raise WriterError(
            f"{path} must be a root-owned directory with no group/other access", EXIT_UNTRUSTED
        )


def _trusted_file(path: str, *, private: bool = True) -> os.stat_result:
    uid, _ = _owner()
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise WriterError(f"{path} is missing; run radon-ib-gateway-credential migrate", EXIT_UNTRUSTED)
    if not stat.S_ISREG(st.st_mode) or st.st_uid != uid:
        raise WriterError(f"{path} must be a root-owned regular, non-symlink file", EXIT_UNTRUSTED)
    if private and st.st_mode & 0o077:
        raise WriterError(f"{path} must have no group/other access", EXIT_UNTRUSTED)
    if not private and st.st_mode & 0o022:
        raise WriterError(f"{path} must not be group/other-writable", EXIT_UNTRUSTED)
    return st


def atomic_write(path: str, data: bytes, uid: int, gid: int, mode: int) -> None:
    """Temp file in the same directory, fsync, chown/chmod, rename, dir fsync."""
    directory = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix="." + os.path.basename(path) + ".")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            os.fchown(handle.fileno(), uid, gid)
            os.fchmod(handle.fileno(), mode)
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise WriterError(f"could not write {path}: {errno.errorcode.get(exc.errno, 'error')}", 1)
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


class _Lock:
    def __init__(self, path: str) -> None:
        self.path = path
        self.fd = -1

    def __enter__(self) -> "_Lock":
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(self.fd)
            raise WriterError("another credential update is in progress", EXIT_BUSY)
        return self

    def __exit__(self, *exc) -> None:
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)


def _read_stdin_values() -> dict:
    raw = sys.stdin.buffer.read(MAX_STDIN_BYTES + 1)
    if len(raw) > MAX_STDIN_BYTES:
        raise WriterError(f"stdin exceeds {MAX_STDIN_BYTES} bytes", EXIT_INVALID)
    try:
        values = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise WriterError("stdin must be a JSON object", EXIT_INVALID)
    if not isinstance(values, dict):
        raise WriterError("stdin must be a JSON object", EXIT_INVALID)
    unknown = sorted(str(key)[:40] for key in values if key not in CREDENTIAL_KEYS)
    if unknown:
        raise WriterError(f"unsupported keys: {', '.join(unknown)}", EXIT_INVALID)
    if "TWS_PASSWORD" not in values:
        raise WriterError("TWS_PASSWORD is required", EXIT_INVALID)
    for key in CREDENTIAL_KEYS:
        if key in values:
            reason = value_error(values[key])
            if reason:
                raise WriterError(f"{key} {reason}", EXIT_INVALID)
    return values


def cmd_write() -> str:
    _require_root()
    os.umask(0o077)
    values = _read_stdin_values()
    paths = _paths()
    _trusted_dir(paths["dir"])
    st = _trusted_file(paths["target"])
    with _Lock(paths["lock"]):
        st = _trusted_file(paths["target"])
        with open(paths["target"], "rb") as handle:
            old = handle.read()
        lines = _split_lines(old.decode("utf-8"))
        for key in values:
            hits = [i for i, line in enumerate(lines) if line.startswith(key + "=")]
            if len(hits) > 1:
                raise WriterError(f"{key} appears {len(hits)} times in {paths['target']}", EXIT_INVALID)
        uid, gid = _owner()
        atomic_write(paths["prev"], old, uid, gid, 0o600)
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        for key in CREDENTIAL_KEYS:
            if key not in values:
                continue
            rendered = _render(key, values[key])
            for i, line in enumerate(lines):
                if line.startswith(key + "="):
                    lines[i] = rendered
                    break
            else:
                lines.append(rendered)
        atomic_write(
            paths["target"], "".join(lines).encode("utf-8"),
            st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode),
        )
    return "updated: " + " ".join(key for key in ("TWS_PASSWORD", "TWS_USERID") if key in values)


def _parse_value(key: str, raw: str) -> str:
    """Literal value of one env-file assignment, or a refusal naming the key."""
    if raw.startswith("'"):
        if len(raw) >= 2 and raw.endswith("'") and "'" not in raw[1:-1]:
            return raw[1:-1]
    elif raw.startswith('"'):
        inner = raw[1:-1]
        if len(raw) >= 2 and raw.endswith('"') and not set(inner) & set('$\\"'):
            return inner
    elif not re.search(r"[\s#'\"$\\]", raw):
        return raw
    raise WriterError(f"{key}: value is not a plain literal; quote it with single quotes", EXIT_INVALID)


def _read_env_file(path: str, keys: frozenset) -> tuple[dict, list]:
    """(allowlisted key -> literal value, other key names). Last one wins."""
    found: dict = {}
    others: list = []
    with open(path, "rb") as handle:
        text = handle.read().decode("utf-8")
    for line in text.split("\n"):
        match = _ASSIGNMENT.match(line.strip())
        if not match:
            continue
        key, raw = match.group(1), match.group(2)
        if key in keys:
            found[key] = _parse_value(key, raw)
        else:
            others.append(key)
    return found, others


def cmd_migrate() -> str:
    _require_root()
    os.umask(0o077)
    paths = _paths()
    uid, gid = _owner()
    if not os.path.lexists(paths["dir"]):
        os.mkdir(paths["dir"], 0o700)
        os.chown(paths["dir"], uid, gid)
    _trusted_dir(paths["dir"])
    with _Lock(paths["lock"]):
        copied, others = _read_env_file(paths["root_env"], GATEWAY_ENV_KEYS)
        if os.path.lexists(paths["target"]):
            _trusted_file(paths["target"])
            current, _ = _read_env_file(paths["target"], frozenset(CREDENTIAL_KEYS))
            copied.update(current)
        if not copied.get("TWS_PASSWORD"):
            raise WriterError(
                "TWS_PASSWORD is in neither gateway.env nor /etc/radon/env", EXIT_INVALID
            )
        for key, value in copied.items():
            if key in CREDENTIAL_KEYS or value != "":
                reason = value_error(value)
                if reason:
                    raise WriterError(f"{key} {reason}", EXIT_INVALID)
        ordered = [key for key in CREDENTIAL_KEYS if key in copied]
        ordered += sorted(key for key in copied if key not in CREDENTIAL_KEYS)
        body = _HEADER + "".join(_render(key, copied[key]) for key in ordered)
        atomic_write(paths["target"], body.encode("utf-8"), uid, gid, 0o600)
    review = sorted({key for key in others if _REVIEW_PREFIX.match(key)})
    message = "copied: " + " ".join(ordered)
    if review:
        message += "\nWARNING not copied (not allowlisted, review): " + " ".join(review)
    return message


def _is_credential_line(line: str) -> bool:
    """Matched the way _read_env_file reads it: systemd strips leading
    whitespace, so an indented TWS_* assignment still reaches every unit."""
    match = _ASSIGNMENT.match(line.strip())
    return bool(match) and match.group(1) in CREDENTIAL_KEYS


def cmd_prune_root_env() -> str:
    _require_root()
    os.umask(0o077)
    paths = _paths()
    _trusted_dir(paths["dir"])
    _trusted_file(paths["target"])
    current, _ = _read_env_file(paths["target"], frozenset(CREDENTIAL_KEYS))
    if not current.get("TWS_PASSWORD"):
        raise WriterError("gateway.env has no TWS_PASSWORD; run migrate first", EXIT_INVALID)
    st = _trusted_file(paths["root_env"], private=False)
    with open(paths["root_env"], "rb") as handle:
        old = handle.read()
    lines = _split_lines(old.decode("utf-8"))
    kept = [line for line in lines if not _is_credential_line(line)]
    if len(kept) == len(lines):
        return "nothing to prune"
    uid, gid = _owner()
    if not os.path.lexists(paths["root_env_backup"]):
        atomic_write(paths["root_env_backup"], old, uid, gid, 0o600)
    atomic_write(
        paths["root_env"], "".join(kept).encode("utf-8"),
        st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode),
    )
    return f"pruned TWS_PASSWORD TWS_USERID from {paths['root_env']}; backup {paths['root_env_backup']}"


def cmd_rollback() -> str:
    _require_root()
    os.umask(0o077)
    paths = _paths()
    _trusted_dir(paths["dir"])
    with _Lock(paths["lock"]):
        _trusted_file(paths["prev"])
        st = _trusted_file(paths["target"])
        with open(paths["prev"], "rb") as handle:
            data = handle.read()
        atomic_write(paths["target"], data, st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode))
    return f"restored {paths['target']} from {paths['prev']}"


COMMANDS = {
    "write": cmd_write,
    "migrate": cmd_migrate,
    "prune-root-env": cmd_prune_root_env,
    "rollback": cmd_rollback,
}


def main(argv: list) -> int:
    if len(argv) != 1 or argv[0] not in COMMANDS:
        print("usage: radon-ib-gateway-credential {write|migrate|prune-root-env|rollback}", file=sys.stderr)
        return EXIT_USAGE
    try:
        print(COMMANDS[argv[0]]())
    except WriterError as exc:
        print(f"radon-ib-gateway-credential: {exc}", file=sys.stderr)
        return exc.code
    except OSError as exc:
        print(
            f"radon-ib-gateway-credential: {exc.filename or 'file'}: "
            f"{errno.errorcode.get(exc.errno or 0, 'error')}",
            file=sys.stderr,
        )
        return 1
    except UnicodeDecodeError:
        print("radon-ib-gateway-credential: an env file is not UTF-8", file=sys.stderr)
        return EXIT_INVALID
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
