#!/usr/bin/env python3
"""Checked-in post-deploy hooks and read-only post-deploy verification.

`deploy.sh` calls `post_deploy.py run` after a release is verified, its green
marker is written and its transition is committed. Two things happen:

1. Hooks. Each reviewed script in `cloud/post_deploy/hooks/` that
   `cloud/post_deploy/manifest.json` pins by SHA-256 runs once, in numeric
   order, as the deploy user (never root). Completion is fsynced to a state
   file, so a later deploy skips it. A failed, timed-out or interrupted hook
   stays pending, stops the hooks after it, is paged, and is retried on the
   next deploy. Hooks must be idempotent: a crash between a hook's side effect
   and its completion record reruns it.
2. Verification. The manifest's declarative `verify` checks (unit active,
   journal pattern present/absent in a window, localhost HTTP probe) run after
   every deploy. They only read. Any failure is paged.

The runner executes only files the manifest pins. It never reads PR bodies,
environment-supplied commands or a shell string. Anything unexpected in the
tree (an unlisted file, a symlink, a group/world-writable path, a hash
mismatch, a foreign owner) refuses the whole run before any hook executes.

Paging reuses `scripts.watchdog.notify.send_direct_page` (DB-free P1) with
only PUSHOVER_USER and PUSHOVER_TOKEN loaded from the env file.

Stdlib only: deploy.sh runs it with the system python, like deploy-journal.py.
Exit codes: 0 clean, 1 hook or check failure, 2 refusal, 64 usage.
"""

from __future__ import annotations

import argparse
import dataclasses
import fcntl
import hashlib
import json
import os
import pwd
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence


MANIFEST_NAME = "manifest.json"
HOOKS_DIRNAME = "hooks"
HOOK_ID_RE = re.compile(r"^([0-9]{4})-[a-z0-9]+(?:-[a-z0-9]+)*$")
HOOK_EXTENSIONS = (".sh", ".py")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_HOOK_TIMEOUT = 120
MAX_HOOK_TIMEOUT = 300
HOOKS_BUDGET_SECONDS = 300
VERIFY_BUDGET_SECONDS = 150
MAX_CHECK_WAIT = 120
MAX_SINCE_MINUTES = 60
MAX_HTTP_TIMEOUT = 10
ERROR_TAIL_CHARS = 1500
PAGE_TAG = "radon-post-deploy"
HOOK_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
# `radon logs <unit>` is the root-owned validating operator's fixed,
# read-only journal tail (sudoers grants `logs radon-*.service`). The deploy
# user is not in systemd-journal, so plain journalctl sees nothing.
DEFAULT_JOURNAL_CMD = ("sudo", "-n", "/usr/local/bin/radon", "logs")
UNIT_RE = re.compile(r"^radon-[a-z0-9]+(?:-[a-z0-9]+)*\.(service|timer)$")
JOURNAL_UNIT_RE = re.compile(r"^radon-[a-z0-9]+(?:-[a-z0-9]+)*\.service$")
CHECK_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
LOCAL_URL_RE = re.compile(r"^http://(?:localhost|127\.0\.0\.1):([0-9]{2,5})(/[A-Za-z0-9._~/?=&%-]*)?$")

HOOK_KEYS = {"id", "sha256", "description", "timeout_seconds"}
CHECK_COMMON = {"id", "type", "description", "wait_seconds"}
CHECK_KEYS = {
    "unit_active": CHECK_COMMON | {"unit"},
    "journal": CHECK_COMMON | {"unit", "pattern", "expect", "since_minutes"},
    "http": CHECK_COMMON | {"url", "expect_status", "contains", "timeout_seconds"},
}

Pager = Callable[[str, str], Optional[str]]


class Refusal(Exception):
    """The tree, manifest, state or caller is not trustworthy. Nothing runs."""


@dataclasses.dataclass(frozen=True)
class Hook:
    id: str
    number: int
    path: Path
    sha256: str
    interpreter: str
    timeout: int


@dataclasses.dataclass(frozen=True)
class HookContext:
    radon_dir: Path
    deploy_sha: str
    env_file: Path
    python: Path
    bash: str = "/bin/bash"
    budget_seconds: float = HOOKS_BUDGET_SECONDS


@dataclasses.dataclass(frozen=True)
class VerifyContext:
    systemctl: str = "/usr/bin/systemctl"
    journal_cmd: Sequence[str] = DEFAULT_JOURNAL_CMD
    since_epoch: Optional[float] = None
    budget_seconds: float = VERIFY_BUDGET_SECONDS
    poll_seconds: float = 5.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def log(message: str) -> None:
    print(f"[post-deploy] {message}", flush=True)


# -- trust checks -------------------------------------------------------------


def _check_path(path: Path, *, kind: str, euid: int, check_owner: bool = True) -> os.stat_result:
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        raise Refusal(f"{path}: missing") from None
    if stat.S_ISLNK(st.st_mode):
        raise Refusal(f"{path}: is a symlink")
    want = stat.S_ISDIR if kind == "dir" else stat.S_ISREG
    if not want(st.st_mode):
        raise Refusal(f"{path}: not a regular {kind}")
    if st.st_mode & 0o022:
        raise Refusal(f"{path}: group/world writable (mode {oct(st.st_mode & 0o777)})")
    if check_owner and st.st_uid not in (euid, 0):
        raise Refusal(f"{path}: owned by uid {st.st_uid}, expected {euid} or root")
    return st


def _int_field(entry: dict, key: str, *, low: int, high: int, default: int, where: str) -> int:
    value = entry.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise Refusal(f"{where}: {key} must be an integer in [{low}, {high}]")
    return value


def load_manifest(root: Path, *, euid: Optional[int] = None, check_owner: bool = True) -> dict:
    """Parse and structurally validate the manifest. Raises Refusal."""
    euid = os.geteuid() if euid is None else euid
    path = root / MANIFEST_NAME
    _check_path(root, kind="dir", euid=euid, check_owner=check_owner)
    _check_path(path, kind="file", euid=euid, check_owner=check_owner)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refusal(f"{path}: unreadable manifest ({exc})") from None
    if not isinstance(data, dict) or set(data) != {"version", "hooks", "verify"} or data["version"] != 1:
        raise Refusal(f"{path}: manifest must be exactly {{version: 1, hooks: [...], verify: [...]}}")
    if not isinstance(data["hooks"], list) or not isinstance(data["verify"], list):
        raise Refusal(f"{path}: hooks and verify must be lists")
    for entry in data["hooks"]:
        if not isinstance(entry, dict):
            raise Refusal(f"{path}: every hook entry must be an object")
        where = f"hook {entry.get('id')!r}"
        unknown = set(entry) - HOOK_KEYS
        if unknown:
            raise Refusal(f"{where}: unknown key(s) {sorted(unknown)}")
        if not isinstance(entry.get("id"), str) or not HOOK_ID_RE.match(entry["id"]):
            raise Refusal(f"{where}: invalid hook id (want NNNN-lower-kebab)")
        if not isinstance(entry.get("sha256"), str) or not SHA256_RE.match(entry["sha256"]):
            raise Refusal(f"{where}: sha256 must be 64 lowercase hex")
        if not isinstance(entry.get("description"), str) or not entry["description"].strip():
            raise Refusal(f"{where}: description is required")
        _int_field(entry, "timeout_seconds", low=1, high=MAX_HOOK_TIMEOUT,
                   default=DEFAULT_HOOK_TIMEOUT, where=where)
    return data


def validate_tree(root: Path, manifest: dict, *, euid: int, check_owner: bool = True) -> list[Hook]:
    """Map manifest entries to pinned files; refuse anything else in hooks/."""
    hooks_dir = root / HOOKS_DIRNAME
    entries = manifest["hooks"]
    for entry in entries:
        if not isinstance(entry.get("id"), str) or not HOOK_ID_RE.match(entry["id"]):
            raise Refusal(f"hook {entry.get('id')!r}: invalid hook id (want NNNN-lower-kebab)")
    if not os.path.lexists(hooks_dir):
        if entries:
            raise Refusal(f"{hooks_dir}: missing, but the manifest lists hooks")
        return []
    _check_path(hooks_dir, kind="dir", euid=euid, check_owner=check_owner)

    allowed = {entry["id"] for entry in entries}
    present: dict[str, str] = {}
    for name in sorted(os.listdir(hooks_dir)):
        stem, ext = os.path.splitext(name)
        if ext not in HOOK_EXTENSIONS or stem not in allowed:
            raise Refusal(f"{hooks_dir / name}: not allowlisted in {MANIFEST_NAME}")
        if stem in present:
            raise Refusal(f"{hooks_dir}: hook {stem} has more than one file")
        present[stem] = name

    hooks: list[Hook] = []
    numbers: dict[int, str] = {}
    for entry in entries:
        hook_id = entry["id"]
        number = int(HOOK_ID_RE.match(hook_id).group(1))
        if number in numbers:
            raise Refusal(f"duplicate hook number {number:04d}: {numbers[number]} and {hook_id}")
        numbers[number] = hook_id
        if hook_id not in present:
            raise Refusal(f"hook {hook_id}: no hook file hooks/{hook_id}.sh or .py")
        path = hooks_dir / present[hook_id]
        _check_path(path, kind="file", euid=euid, check_owner=check_owner)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != entry["sha256"]:
            raise Refusal(f"hook {hook_id}: sha256 mismatch (file {digest}, manifest {entry['sha256']})")
        hooks.append(Hook(
            id=hook_id,
            number=number,
            path=path,
            sha256=digest,
            interpreter="python" if path.suffix == ".py" else "bash",
            timeout=entry.get("timeout_seconds", DEFAULT_HOOK_TIMEOUT),
        ))
    return sorted(hooks, key=lambda hook: hook.number)


def validate_checks(checks: list) -> list[dict]:
    """Structurally validate declarative read-only checks. Raises Refusal."""
    seen: set[str] = set()
    out: list[dict] = []
    for check in checks:
        if not isinstance(check, dict):
            raise Refusal("every verify entry must be an object")
        check_id = check.get("id")
        where = f"check {check_id!r}"
        if not isinstance(check_id, str) or not CHECK_ID_RE.match(check_id):
            raise Refusal(f"{where}: invalid check id (want lower-kebab)")
        if check_id in seen:
            raise Refusal(f"duplicate check id {check_id}")
        seen.add(check_id)
        kind = check.get("type")
        if kind not in CHECK_KEYS:
            raise Refusal(f"{where}: unknown check type {kind!r}")
        unknown = set(check) - CHECK_KEYS[kind]
        if unknown:
            raise Refusal(f"{where}: unknown key(s) {sorted(unknown)}")
        if not isinstance(check.get("description"), str) or not check["description"].strip():
            raise Refusal(f"{where}: description is required")
        _int_field(check, "wait_seconds", low=0, high=MAX_CHECK_WAIT, default=0, where=where)
        if kind in ("unit_active", "journal"):
            unit = check.get("unit")
            pattern = JOURNAL_UNIT_RE if kind == "journal" else UNIT_RE
            if not isinstance(unit, str) or not pattern.match(unit) or unit.startswith("radon-beta-"):
                raise Refusal(f"{where}: unit must be a radon-*.service{'' if kind == 'journal' else '/.timer'} name")
        if kind == "journal":
            try:
                re.compile(check.get("pattern") if isinstance(check.get("pattern"), str) else "(")
            except re.error:
                raise Refusal(f"{where}: pattern is not a valid regular expression") from None
            if check.get("expect") not in ("present", "absent"):
                raise Refusal(f"{where}: expect must be 'present' or 'absent'")
            _int_field(check, "since_minutes", low=1, high=MAX_SINCE_MINUTES, default=-1, where=where)
        if kind == "http":
            url = check.get("url")
            if not isinstance(url, str) or not LOCAL_URL_RE.match(url):
                raise Refusal(f"{where}: url must be http://localhost or http://127.0.0.1 with a port")
            _int_field(check, "expect_status", low=100, high=599, default=200, where=where)
            _int_field(check, "timeout_seconds", low=1, high=MAX_HTTP_TIMEOUT, default=5, where=where)
            if "contains" in check and not isinstance(check["contains"], str):
                raise Refusal(f"{where}: contains must be a string")
        out.append(check)
    return out


# -- durable state ------------------------------------------------------------


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def load_state(path: Path) -> dict:
    if os.path.islink(path):
        raise Refusal(f"{path}: state file is a symlink")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return {"version": 1, "hooks": {}}
    except OSError as exc:
        raise Refusal(f"{path}: state unreadable ({exc})") from None
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        raw = handle.read()
    try:
        state = json.loads(raw)
    except ValueError:
        raise Refusal(f"{path}: state is not valid JSON; refusing to rerun every hook") from None
    if not isinstance(state, dict) or state.get("version") != 1 or not isinstance(state.get("hooks"), dict):
        raise Refusal(f"{path}: state has an unexpected schema; refusing to rerun every hook")
    return state


def save_state(path: Path, state: dict) -> None:
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
    _fsync_dir(path.parent)


class _StateLock:
    def __init__(self, state_path: Path):
        self.path = Path(f"{state_path}.lock")
        self.fd: Optional[int] = None

    def __enter__(self) -> "_StateLock":
        self.fd = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(self.fd)
            self.fd = None
            raise Refusal(f"{self.path}: another post-deploy runner holds the lock") from None
        return self

    def __exit__(self, *exc) -> None:
        if self.fd is not None:
            os.close(self.fd)


# -- hooks --------------------------------------------------------------------


def _hook_env(hook: Hook, ctx: HookContext) -> dict[str, str]:
    radon_dir = str(ctx.radon_dir)
    return {
        "PATH": HOOK_PATH,
        "HOME": pwd.getpwuid(os.geteuid()).pw_dir,
        "LANG": "C.UTF-8",
        "PYTHONPATH": f"{radon_dir}{os.pathsep}{radon_dir}/scripts",
        "RADON_DIR": radon_dir,
        "RADON_DEPLOY_SHA": ctx.deploy_sha,
        "RADON_ENV_FILE": str(ctx.env_file),
        "RADON_PYTHON": str(ctx.python),
        "RADON_POST_DEPLOY_HOOK_ID": hook.id,
    }


def _terminate_group(proc: subprocess.Popen) -> None:
    for sig, grace in ((signal.SIGTERM, 5), (signal.SIGKILL, 5)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            return
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            continue
        # The leader exited; sweep anything left in its group.
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return


def execute_hook(hook: Hook, ctx: HookContext, timeout: float) -> tuple[bool, str]:
    interpreter = str(ctx.python) if hook.interpreter == "python" else ctx.bash
    proc = subprocess.Popen(
        [interpreter, str(hook.path)],
        cwd=ctx.radon_dir,
        env=_hook_env(hook, ctx),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_group(proc)
        output, _ = proc.communicate()
        text = output.decode("utf-8", "replace")
        sys.stdout.write(text)
        return False, f"timed out after {timeout:.0f}s; {text[-ERROR_TAIL_CHARS:]}"
    text = output.decode("utf-8", "replace")
    sys.stdout.write(text)
    sys.stdout.flush()
    # Also sweep background children the hook left in its session.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    if proc.returncode != 0:
        return False, f"exit {proc.returncode}; {text[-ERROR_TAIL_CHARS:]}"
    return True, ""


def _page(pager: Pager, title: str, message: str) -> bool:
    log(f"PAGE {title}: {message}")
    error = pager(title, message)
    if error:
        log(f"page delivery failed: {error}")
        return False
    return True


def run_hooks(hooks: list[Hook], *, state_path: Path, ctx: HookContext, pager: Pager) -> int:
    """Run pending hooks once each. 0 clean, 1 a hook failed or was deferred."""
    if not hooks:
        return 0
    state_path.parent.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    with _StateLock(state_path):
        state = load_state(state_path)
        records = state["hooks"]
        deadline = time.monotonic() + ctx.budget_seconds
        for hook in hooks:
            record = records.get(hook.id) or {}
            if record.get("status") == "done":
                if record.get("sha256") != hook.sha256:
                    problems.append(f"{hook.id} changed after it completed; it will not rerun. "
                                    f"Add a new numbered hook instead.")
                continue
            remaining = deadline - time.monotonic()
            if remaining < 1:
                problems.append(f"{hook.id} and later hooks deferred: post-deploy hook budget "
                                f"({ctx.budget_seconds:.0f}s) exhausted; retried next deploy")
                break
            attempts = int(record.get("attempts") or 0) + 1
            record = {
                "status": "running",
                "sha256": hook.sha256,
                "attempts": attempts,
                "deploy_sha": ctx.deploy_sha,
                "started_at": _now_iso(),
            }
            records[hook.id] = record
            save_state(state_path, state)
            log(f"running {hook.id} (attempt {attempts})")
            ok, error = execute_hook(hook, ctx, min(hook.timeout, remaining))
            record["finished_at"] = _now_iso()
            if ok:
                record["status"] = "done"
                record["completed_at"] = record["finished_at"]
                record.pop("last_error", None)
                save_state(state_path, state)
                log(f"{hook.id} done")
                continue
            record["status"] = "failed"
            record["last_error"] = error
            save_state(state_path, state)
            problems.append(f"{hook.id} failed on attempt {attempts} ({ctx.deploy_sha[:7]}): "
                            f"{error.strip()[-400:]}. Later hooks wait; retried next deploy.")
            break
    if not problems:
        return 0
    _page(pager, "radon post-deploy hook failed", "\n".join(problems))
    return 1


# -- verification -------------------------------------------------------------


def _check_unit_active(check: dict, ctx: VerifyContext) -> Optional[str]:
    result = subprocess.run(
        [ctx.systemctl, "is-active", "--quiet", check["unit"]],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
    )
    return None if result.returncode == 0 else f"{check['unit']} is not active (rc {result.returncode})"


def _journal_message(value) -> str:
    if isinstance(value, list):
        try:
            return bytes(value).decode("utf-8", "replace")
        except (TypeError, ValueError):
            return ""
    return value if isinstance(value, str) else ""


def _check_journal(check: dict, ctx: VerifyContext) -> Optional[str]:
    result = subprocess.run(
        [*ctx.journal_cmd, check["unit"]],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return f"journal read for {check['unit']} failed (rc {result.returncode}): {result.stderr.strip()[:200]}"
    window_start = time.time() - check["since_minutes"] * 60
    if ctx.since_epoch is not None:
        window_start = max(window_start, ctx.since_epoch)
    pattern = re.compile(check["pattern"])
    found = False
    for line in result.stdout.splitlines():
        try:
            entry = json.loads(line)
            ts = int(entry["__REALTIME_TIMESTAMP"]) / 1_000_000
        except (ValueError, KeyError, TypeError):
            continue
        if ts >= window_start and pattern.search(_journal_message(entry.get("MESSAGE"))):
            found = True
            break
    if check["expect"] == "present" and not found:
        return f"{check['unit']}: /{check['pattern']}/ not logged in the last {check['since_minutes']}m"
    if check["expect"] == "absent" and found:
        return f"{check['unit']}: /{check['pattern']}/ logged in the last {check['since_minutes']}m"
    return None


def _check_http(check: dict, ctx: VerifyContext) -> Optional[str]:
    want = check.get("expect_status", 200)
    request = urllib.request.Request(check["url"], method="GET")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=check.get("timeout_seconds", 5)) as response:
            status, body = response.status, response.read(65536)
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read(65536) if hasattr(exc, "read") else b""
    except (OSError, urllib.error.URLError) as exc:
        return f"GET {check['url']} failed: {exc}"
    if status != want:
        return f"GET {check['url']} returned {status}, expected {want}"
    if "contains" in check and check["contains"] not in body.decode("utf-8", "replace"):
        return f"GET {check['url']} body lacks {check['contains']!r}"
    return None


CHECKS = {"unit_active": _check_unit_active, "journal": _check_journal, "http": _check_http}


def run_checks(checks: list[dict], ctx: VerifyContext) -> list[str]:
    """Evaluate read-only checks; return one 'id: reason' per failure."""
    failures: list[str] = []
    deadline = time.monotonic() + ctx.budget_seconds
    for check in checks:
        func = CHECKS[check["type"]]
        wait_until = min(time.monotonic() + check.get("wait_seconds", 0), deadline)
        absent = check["type"] == "journal" and check["expect"] == "absent"
        if absent:
            # An absence check watches the whole wait window, then decides once.
            time.sleep(max(0.0, wait_until - time.monotonic()))
        while True:
            try:
                reason = func(check, ctx)
            except (OSError, subprocess.SubprocessError) as exc:
                reason = f"check could not run: {exc}"
            if reason is None or absent or time.monotonic() >= wait_until:
                break
            time.sleep(min(ctx.poll_seconds, max(0.0, wait_until - time.monotonic())))
        if reason is None:
            log(f"verify {check['id']}: ok")
        else:
            log(f"verify {check['id']}: FAIL {reason}")
            failures.append(f"{check['id']}: {reason}")
    return failures


# -- orchestration ------------------------------------------------------------


def make_pager(*, radon_dir: Path, env_file: Path, python: Path) -> Pager:
    """P1 through scripts.watchdog.notify.send_direct_page, isolated in a child."""

    def pager(title: str, message: str) -> Optional[str]:
        env = {"PATH": HOOK_PATH, "LANG": "C.UTF-8",
               "PYTHONPATH": f"{radon_dir}{os.pathsep}{radon_dir}/scripts"}
        env.update(_pushover_creds(env_file))
        code = (
            "import sys\n"
            "from scripts.watchdog import notify\n"
            "err = notify.send_direct_page(title=sys.argv[1], message=sys.argv[2], tag=sys.argv[3])\n"
            "print(err or '')\n"
            "sys.exit(1 if err else 0)\n"
        )
        try:
            result = subprocess.run(
                [str(python), "-c", code, title, message[:900], PAGE_TAG],
                cwd=radon_dir, env=env, stdin=subprocess.DEVNULL,
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return f"pager could not run: {exc}"
        if result.returncode != 0:
            return (result.stdout.strip() or result.stderr.strip()[-300:] or f"pager exit {result.returncode}")
        return None

    return pager


def _pushover_creds(env_file: Path) -> dict[str, str]:
    """Read only PUSHOVER_USER / PUSHOVER_TOKEN; never load the full env."""
    creds: dict[str, str] = {}
    try:
        lines = env_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return creds
    for line in lines:
        key, sep, value = line.strip().partition("=")
        key = key.removeprefix("export ").strip()
        if not sep or key not in ("PUSHOVER_USER", "PUSHOVER_TOKEN"):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        creds[key] = value
    return creds


def _page_log_pager(path: str) -> Pager:
    def pager(title: str, message: str) -> Optional[str]:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"title": title, "message": message}) + "\n")
        return None

    return pager


def run(*, root: Path, state_path: Path, hook_ctx: HookContext, verify_ctx: VerifyContext,
        expect_user: str, pager: Pager) -> int:
    euid = os.geteuid()
    try:
        if euid == 0:
            raise Refusal("refusing to run post-deploy hooks as root")
        actual = pwd.getpwuid(euid).pw_name
        if actual != expect_user:
            raise Refusal(f"running as {actual}, expected {expect_user}")
        manifest = load_manifest(root, euid=euid)
        hooks = validate_tree(root, manifest, euid=euid)
        checks = validate_checks(manifest["verify"])
    except Refusal as exc:
        _page(pager, "radon post-deploy refused", f"{exc}. No hook ran.")
        return 2

    # Every page below coincides with a nonzero status; a failed delivery is
    # logged by _page and the deploy log carries the same text.
    status = 0
    try:
        status = run_hooks(hooks, state_path=state_path, ctx=hook_ctx, pager=pager)
    except Refusal as exc:
        _page(pager, "radon post-deploy refused", f"{exc}. No further hook ran.")
        status = 2
    failures = run_checks(checks, verify_ctx) if checks else []
    if failures:
        _page(pager, "radon post-deploy verification failed",
              f"{hook_ctx.deploy_sha[:7]}: " + "; ".join(failures))
        status = max(status, 1)
    return status


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="post_deploy")
    sub = parser.add_subparsers(dest="command", required=True)
    run_p = sub.add_parser("run", help="run pending hooks, then verification")
    run_p.add_argument("--root", required=True, type=Path)
    run_p.add_argument("--state", required=True, type=Path)
    run_p.add_argument("--deploy-sha", required=True)
    run_p.add_argument("--radon-dir", required=True, type=Path)
    run_p.add_argument("--env-file", required=True, type=Path)
    run_p.add_argument("--python", required=True, type=Path)
    run_p.add_argument("--expect-user", required=True)
    run_p.add_argument("--since-epoch", type=float, default=None)
    check_p = sub.add_parser("check", help="validate a manifest and its pinned hooks (no execution)")
    check_p.add_argument("--root", required=True, type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 64 if exc.code else 0

    if args.command == "check":
        try:
            manifest = load_manifest(args.root, check_owner=False)
            hooks = validate_tree(args.root, manifest, euid=os.geteuid(), check_owner=False)
            checks = validate_checks(manifest["verify"])
        except Refusal as exc:
            print(f"post_deploy check: {exc}", file=sys.stderr)
            return 2
        print(f"post_deploy check: {len(hooks)} hook(s), {len(checks)} check(s) valid")
        return 0

    if not re.fullmatch(r"[0-9a-f]{40}", args.deploy_sha):
        print("post_deploy: --deploy-sha must be a 40-hex commit", file=sys.stderr)
        return 64
    page_log = os.environ.get("RADON_POST_DEPLOY_PAGE_LOG")
    pager = _page_log_pager(page_log) if page_log else make_pager(
        radon_dir=args.radon_dir, env_file=args.env_file, python=args.python)
    hook_ctx = HookContext(radon_dir=args.radon_dir, deploy_sha=args.deploy_sha,
                           env_file=args.env_file, python=args.python)
    verify_ctx = VerifyContext(since_epoch=args.since_epoch)
    return run(root=args.root, state_path=args.state, hook_ctx=hook_ctx, verify_ctx=verify_ctx,
               expect_user=args.expect_user, pager=pager)


if __name__ == "__main__":
    sys.exit(main())
