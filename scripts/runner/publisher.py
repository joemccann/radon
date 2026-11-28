#!/usr/bin/env python3
"""Credential publisher for the Mac runner.

The daemon runs as `_radonpub` and is the only account that reads the
GitHub token, the reports deploy key, and the Pushover keys. A caller
on the runner uid sends one JSON object over a local socket. The only
operations are:

- push: the caller's own `<prefix>/<YYYY-MM-DD>` branch
- draft_pr: open or edit a draft pull request for that branch
- publish_report: write one private security report and push it
- notify: send the operator Pushover notification

Merges, force-pushes, other branches, and arbitrary git or API calls
are refused. Every request is logged. `install.sh` does not create the
account, the directory, or the daemon unless the root-owned enable
flag is present, and it never reads or copies existing credentials.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import socket
import time
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

ENABLE_FLAG = "/usr/local/radon-runner/enable-publisher"
DEFAULT_SOCKET = "/Library/Radon/publisher/run/publisher.sock"
DEFAULT_SECRETS = "/Library/Radon/publisher/secrets"
DEFAULT_LOG = "/Library/Radon/publisher/log/requests.log"
DEFAULT_TMP = "/Library/Radon/publisher/tmp"
RADON_REMOTE = "https://github.com/joemccann/radon.git"
RADON_REPO = "joemccann/radon"
REPORTS_REMOTE = "git@github.com:joemccann/radon-security-reports.git"
REPORTS_WEB = "https://github.com/joemccann/radon-security-reports/blob/main"
GITHUB_HOSTKEY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"
PUSHOVER_URL = "https://api.pushover.net/1/messages.json"
MAX_REQUEST = 256 * 1024
# Per-connection bound in the long-lived serve loop (serve_once takes its own).
CONNECTION_TIMEOUT = 30.0
MAX_BUNDLE = 20 * 1024 * 1024
MAX_REPORT = 1024 * 1024
MAX_SECRET = 16 * 1024

CALLERS = frozenset(
    {
        "documentation",
        "ci-performance",
        "performance",
        "testing",
        "reliability",
        "security",
        "security-deepsec",
    }
)
REPORT_CALLERS = frozenset({"security", "security-deepsec"})
OPS = frozenset({"push", "draft_pr", "publish_report", "notify"})
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_PHASE = re.compile(r"[a-z][a-z0-9-]{0,32}")
_PR = re.compile(r"[1-9][0-9]{0,9}")
_LOG_TOKEN = re.compile(r"[a-z0-9_./-]{1,80}")
_SECRET_LINE = re.compile(r"[A-Za-z0-9._~+/=_-]{8,256}")
_PUSH_KEYS = frozenset({"op", "caller", "branch", "bundle"})
_DRAFT_OPEN = frozenset({"op", "caller", "branch", "action", "title", "body"})
_DRAFT_EDIT = _DRAFT_OPEN | {"pr"}
_REPORT_KEYS = frozenset({"op", "caller", "phase", "date", "path"})
_NOTIFY_KEYS = frozenset({"op", "caller", "title", "message", "url", "url_title"})


class Rejected(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FlagStat:
    def __init__(
        self,
        *,
        exists: bool,
        is_symlink: bool,
        is_reg: bool,
        uid: int,
        mode: int,
        parent_uid: int,
        parent_mode: int,
        parent_is_symlink: bool,
    ) -> None:
        self.exists = exists
        self.is_symlink = is_symlink
        self.is_reg = is_reg
        self.uid = uid
        self.mode = mode
        self.parent_uid = parent_uid
        self.parent_mode = parent_mode
        self.parent_is_symlink = parent_is_symlink


def decide_flag(info: FlagStat) -> tuple[bool, str]:
    """The flag enables the publisher only when root owns it and the bot cannot replace it."""
    if not info.exists:
        return False, "missing"
    if info.is_symlink or info.parent_is_symlink:
        return False, "symlink"
    if not info.is_reg:
        return False, "type"
    if info.uid != 0:
        return False, "owner"
    if info.mode & 0o022:
        return False, "writable"
    if info.parent_uid != 0 or info.parent_mode & 0o022:
        return False, "parent"
    return True, "ok"


def load_flag(path: Path) -> FlagStat:
    parent = path.parent
    try:
        parent_stat = os.lstat(parent)
    except OSError:
        return FlagStat(
            exists=False,
            is_symlink=False,
            is_reg=False,
            uid=-1,
            mode=0,
            parent_uid=-1,
            parent_mode=0,
            parent_is_symlink=False,
        )
    parent_link = stat.S_ISLNK(parent_stat.st_mode)
    parent_mode = stat.S_IMODE(parent_stat.st_mode)
    try:
        info = os.lstat(path)
    except OSError:
        return FlagStat(
            exists=False,
            is_symlink=False,
            is_reg=False,
            uid=-1,
            mode=0,
            parent_uid=parent_stat.st_uid,
            parent_mode=parent_mode,
            parent_is_symlink=parent_link,
        )
    return FlagStat(
        exists=True,
        is_symlink=stat.S_ISLNK(info.st_mode),
        is_reg=stat.S_ISREG(info.st_mode),
        uid=info.st_uid,
        mode=stat.S_IMODE(info.st_mode),
        parent_uid=parent_stat.st_uid,
        parent_mode=parent_mode,
        parent_is_symlink=parent_link,
    )


def flag_enabled(path: Path) -> tuple[bool, str]:
    return decide_flag(load_flag(path))


class Config:
    def __init__(
        self,
        *,
        client_uid: int,
        bot_home: Path,
        secrets: Path,
        socket_path: Path,
        log_path: Path,
        tmp: Path,
        gitconfig: Path,
        git: str = "/usr/bin/git",
        gh: str = "/opt/homebrew/bin/gh",
        curl: str = "/usr/bin/curl",
        ssh: str = "/usr/bin/ssh",
    ) -> None:
        self.client_uid = client_uid
        self.bot_home = bot_home
        self.secrets = secrets
        self.socket_path = socket_path
        self.log_path = log_path
        self.tmp = tmp
        self.gitconfig = gitconfig
        self.git = git
        self.gh = gh
        self.curl = curl
        self.ssh = ssh

    def work_root(self, caller: str) -> Path:
        return self.bot_home / "radon-runner" / "work" / caller

    def scratch_root(self, caller: str) -> Path:
        return self.bot_home / "radon-runner" / "state" / caller / "scratch"


def _date(value: object) -> str:
    if not isinstance(value, str) or _DATE.fullmatch(value) is None:
        raise Rejected("date")
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise Rejected("date") from exc
    return value


def _caller(value: object, *, allowed: frozenset[str] = CALLERS) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise Rejected("caller")
    return value


def _branch(caller: str, value: object) -> str:
    if not isinstance(value, str):
        raise Rejected("branch")
    if value.startswith("+"):
        raise Rejected("force_push")
    prefix, sep, day = value.partition("/")
    if not sep or "/" in day or prefix != caller or _DATE.fullmatch(day) is None:
        raise Rejected("branch")
    _date(day)
    return value


def _text(value: object, code: str, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or "\x00" in value or "\n" in value or "\r" in value:
        raise Rejected(code)
    if len(value) > limit or (not empty and not value.strip()):
        raise Rejected(code)
    return value


def _keys(data: dict, allowed: frozenset[str]) -> None:
    extra = set(data) - allowed
    if extra:
        if "force" in extra or "force_with_lease" in extra:
            raise Rejected("force_push")
        if "merge" in extra:
            raise Rejected("merge")
        raise Rejected("unknown_field")
    missing = allowed - set(data)
    if missing:
        raise Rejected("missing_field")


def _dangerous(data: dict) -> None:
    op = data.get("op")
    action = data.get("action")
    if op == "merge" or action == "merge":
        raise Rejected("merge")
    if op in {"git", "api", "shell", "exec"} or action in {"git", "api", "shell", "exec"}:
        raise Rejected("command")
    if data.get("force") is True or data.get("force_with_lease") is True:
        raise Rejected("force_push")


def validate(data: object) -> dict:
    """Pure request check. No filesystem and no credential read."""
    if not isinstance(data, dict):
        raise Rejected("json")
    _dangerous(data)
    op = data.get("op")
    if op not in OPS:
        raise Rejected("unknown_op")
    if op == "push":
        _keys(data, _PUSH_KEYS)
        caller = _caller(data.get("caller"))
        branch = _branch(caller, data.get("branch"))
        bundle = _text(data.get("bundle"), "path", 512)
        if not bundle.startswith("/") or ".." in Path(bundle).parts:
            raise Rejected("path")
        return {"op": op, "caller": caller, "branch": branch, "bundle": bundle}
    if op == "draft_pr":
        action = data.get("action")
        if action == "open":
            _keys(data, _DRAFT_OPEN)
        elif action == "edit":
            _keys(data, _DRAFT_EDIT)
        else:
            raise Rejected("action")
        caller = _caller(data.get("caller"))
        branch = _branch(caller, data.get("branch"))
        title = _text(data.get("title"), "title", 240)
        body = data.get("body")
        if not isinstance(body, str) or "\x00" in body or len(body.encode()) > 48_000 or not body.strip():
            raise Rejected("body")
        out = {"op": op, "action": action, "caller": caller, "branch": branch, "title": title, "body": body}
        if action == "edit":
            pr = data.get("pr")
            if not isinstance(pr, str) or _PR.fullmatch(pr) is None:
                raise Rejected("pr")
            out["pr"] = pr
        return out
    if op == "publish_report":
        _keys(data, _REPORT_KEYS)
        caller = _caller(data.get("caller"), allowed=REPORT_CALLERS)
        phase = data.get("phase")
        if not isinstance(phase, str) or _PHASE.fullmatch(phase) is None:
            raise Rejected("phase")
        day = _date(data.get("date"))
        path = _text(data.get("path"), "path", 512)
        if not path.startswith("/") or ".." in Path(path).parts:
            raise Rejected("path")
        return {"op": op, "caller": caller, "phase": phase, "date": day, "path": path}
    _keys(data, _NOTIFY_KEYS)
    caller = _caller(data.get("caller"))
    title = _text(data.get("title"), "title", 250)
    message = _text(data.get("message"), "message", 1024)
    url = _text(data.get("url"), "url", 512, empty=True)
    url_title = _text(data.get("url_title"), "url", 100, empty=True)
    if url:
        _github_url(url)
    elif url_title:
        raise Rejected("url")
    return {"op": op, "caller": caller, "title": title, "message": message, "url": url, "url_title": url_title}


def _github_url(url: str) -> None:
    if not url.startswith("https://github.com/") or "@" in url or "\\" in url:
        raise Rejected("url")
    if "?" in url or "#" in url or any(ch.isspace() for ch in url) or not url.isprintable():
        raise Rejected("url")
    rest = url.removeprefix("https://github.com/")
    segments = rest.split("/")
    if segments[-1] == "":
        segments = segments[:-1]
    if any(segment in ("", ".", "..") for segment in segments):
        raise Rejected("url")
    if rest.startswith("joemccann/radon/") or rest.startswith("joemccann/radon-security-reports/"):
        return
    raise Rejected("url")


def _log_token(value: object) -> str:
    if isinstance(value, str) and _LOG_TOKEN.fullmatch(value):
        return value
    return "-"


def log_request(path: Path, *, peer: int, op: object, caller: object, branch: object, result: str, reason: str) -> None:
    line = (
        f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
        f"peer={peer} op={_log_token(op)} caller={_log_token(caller)} "
        f"branch={_log_token(branch)} result={result} reason={_log_token(reason)}\n"
    )
    if len(line) > 400:
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)


def _relative_parts(root: Path, path: Path) -> tuple[str, ...]:
    if not path.is_absolute():
        raise Rejected("path")
    root_parts = root.parts
    parts = path.parts
    if parts[: len(root_parts)] != root_parts:
        raise Rejected("path")
    rel = parts[len(root_parts) :]
    if not rel or any(part in ("", ".", "..") for part in rel):
        raise Rejected("path")
    return rel


def read_confined(roots: list[Path], path: Path, client_uid: int, limit: int) -> bytes:
    """Read a regular file the caller owns, without following a swapped symlink."""
    chosen: tuple[Path, tuple[str, ...]] | None = None
    for root in roots:
        try:
            rel = _relative_parts(root, path)
        except Rejected:
            continue
        chosen = (root, rel)
        break
    if chosen is None:
        raise Rejected("path")
    root, rel = chosen
    dir_fd = -1
    file_fd = -1
    try:
        dir_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for part in rel[:-1]:
            nxt = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = nxt
        # O_NONBLOCK: a FIFO planted at the name opens at once instead of
        # holding the daemon until a writer shows up; fstat then rejects it.
        file_fd = os.open(rel[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=dir_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            raise Rejected("path")
        if info.st_uid != client_uid or info.st_mode & 0o022:
            raise Rejected("path_owner")
        if info.st_size <= 0 or info.st_size > limit:
            raise Rejected("path_size")
        chunks: list[bytes] = []
        left = info.st_size
        while left:
            blob = os.read(file_fd, min(1024 * 1024, left))
            if not blob:
                break
            chunks.append(blob)
            left -= len(blob)
        data = b"".join(chunks)
        if len(data) != info.st_size:
            raise Rejected("path_size")
        return data
    except Rejected:
        raise
    except OSError as exc:
        raise Rejected("path") from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if dir_fd >= 0:
            os.close(dir_fd)


def peer_uid(conn: socket.socket) -> int:
    if hasattr(socket, "SO_PEERCRED"):
        raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _pid, uid, _gid = __import__("struct").unpack("@iii", raw[:12])
        return int(uid)
    import ctypes

    uid = ctypes.c_uint()
    gid = ctypes.c_uint()
    if ctypes.CDLL(None).getpeereid(conn.fileno(), ctypes.byref(uid), ctypes.byref(gid)) != 0:
        raise Rejected("peer")
    return int(uid.value)


def _recv_all(conn: socket.socket, deadline: float = CONNECTION_TIMEOUT) -> bytes:
    """Read until EOF, bounded by size and by an absolute deadline.

    The socket timeout alone is per recv: a client trickling one byte at a
    time would hold the loop open indefinitely.
    """
    chunks: list[bytes] = []
    total = 0
    end = time.monotonic() + deadline
    while total <= MAX_REQUEST:
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise Rejected("timeout")
        conn.settimeout(remaining)
        try:
            blob = conn.recv(65536)
        except socket.timeout as exc:
            raise Rejected("timeout") from exc
        if not blob:
            break
        total += len(blob)
        if total > MAX_REQUEST:
            raise Rejected("too_large")
        chunks.append(blob)
    return b"".join(chunks)


def _scrub(text: str) -> str:
    if "\x00" in text:
        raise Rejected("report")
    here = Path(__file__).resolve()
    candidates = [here.parents[1], here.parent / "lib", Path("/usr/local/radon-runner/lib")]
    for directory in candidates:
        module = directory / "credential_redaction.py"
        if not module.is_file():
            continue
        sys.path.insert(0, str(directory))
        try:
            from credential_redaction import scrub_credential_text
        except Exception as exc:
            raise Rejected("redaction") from exc
        cleaned = scrub_credential_text(text)
        if not isinstance(cleaned, str):
            raise Rejected("redaction")
        return cleaned
    raise Rejected("redaction")


GIT_LOCK = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=",
    "-c",
    "init.templateDir=",
    "-c",
    "uploadpack.packObjectsHook=",
)


def push_commands(git: str, branch: str, bundle: str, work: str) -> list[list[str]]:
    ref = f"refs/heads/{branch}"
    spec = f"{ref}:{ref}"
    if spec.startswith("+") or branch.startswith("+"):
        raise Rejected("force_push")
    return [
        [git, *GIT_LOCK, "bundle", "verify", bundle],
        [git, *GIT_LOCK, "bundle", "list-heads", bundle],
        [git, *GIT_LOCK, "init", "--bare", work],
        [git, *GIT_LOCK, "--git-dir", work, "fetch", "--no-tags", bundle, spec],
        [git, *GIT_LOCK, "--git-dir", work, "push", RADON_REMOTE, spec],
    ]


def report_push_command(git: str, work: str) -> list[str]:
    return [git, *GIT_LOCK, "-C", work, "push", "--quiet", "origin", "HEAD:main"]


def child_env(cfg: Config, extra: dict[str, str] | None = None) -> dict[str, str]:
    """A clean environment. The caller's token, agent, and proxy never carry in."""
    env = {
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "HOME": "/var/empty",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_GLOBAL": str(cfg.gitconfig),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_TEMPLATE_DIR": "/var/empty",
        "RADON_PUBLISHER_SECRETS": str(cfg.secrets),
    }
    if extra:
        env.update(extra)
    return env


def _one_head(stdout: str, branch: str) -> None:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise Rejected("bundle")
    sha, sep, ref = lines[0].partition(" ")
    if not sep or ref != f"refs/heads/{branch}" or len(sha) not in (40, 64):
        raise Rejected("bundle")
    if any(char not in "0123456789abcdef" for char in sha.lower()):
        raise Rejected("bundle")


class RealOps:
    def __init__(self, cfg: Config, run: Callable[..., subprocess.CompletedProcess[str]] | None = None) -> None:
        self.cfg = cfg
        self._run = run or subprocess.run

    def _exec(
        self, argv: list[str], *, env: dict[str, str], timeout: int, stdin: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        if any(
            arg == "--force" or arg == "--force-with-lease" or arg.startswith("+refs/") or arg.startswith("+HEAD")
            for arg in argv
        ):
            raise Rejected("force_push")
        return self._run(
            argv,
            env=env,
            input=stdin,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    def push(self, caller: str, branch: str, bundle: str) -> dict:
        data = read_confined(
            [self.cfg.work_root(caller), self.cfg.scratch_root(caller)],
            Path(bundle),
            self.cfg.client_uid,
            MAX_BUNDLE,
        )
        work = Path(tempfile.mkdtemp(prefix="push-", dir=self.cfg.tmp))
        copy = work / "in.bundle"
        try:
            copy.write_bytes(data)
            os.chmod(copy, 0o600)
            commands = push_commands(self.cfg.git, branch, str(copy), str(work / "repo.git"))
            env = child_env(self.cfg)
            verify, listed_cmd, *rest = commands
            verified = self._exec(verify, env=env, timeout=60)
            if verified.returncode != 0:
                raise Rejected("push_failed")
            listed = self._exec(listed_cmd, env=env, timeout=30)
            if listed.returncode != 0:
                raise Rejected("push_failed")
            _one_head(listed.stdout, branch)
            for argv in rest:
                proc = self._exec(argv, env=env, timeout=120)
                if proc.returncode != 0:
                    raise Rejected("push_failed")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return {"pushed": branch}

    def open_draft(self, caller: str, branch: str, title: str, body: str) -> dict:
        del caller
        return self._gh_draft(
            ["pr", "create", "--repo", RADON_REPO, "--draft", "--base", "main", "--head", branch], title, body
        )

    def edit_draft(self, caller: str, branch: str, pr: str, title: str, body: str) -> dict:
        del caller
        viewed = self._gh(
            ["pr", "view", pr, "--repo", RADON_REPO, "--json", "isDraft,headRefName,isCrossRepository,headRepositoryOwner"],
            stdin=None,
        )
        try:
            payload = json.loads(viewed.stdout)
        except json.JSONDecodeError as exc:
            raise Rejected("pr_state") from exc
        # A fork can reuse a runner branch name: the edit is confined to a
        # draft whose head lives in this repository.
        owner = payload.get("headRepositoryOwner") or {}
        if (
            payload.get("isDraft") is not True
            or payload.get("headRefName") != branch
            or payload.get("isCrossRepository") is not False
            or owner.get("login") != RADON_REPO.split("/", 1)[0]
        ):
            raise Rejected("not_draft")
        return self._gh_draft(["pr", "edit", pr, "--repo", RADON_REPO], title, body)

    def _gh_draft(self, args: list[str], title: str, body: str) -> dict:
        # The draft reaches a PUBLIC pull request: the same scrub the private
        # report path applies, at the one place both draft actions pass.
        title = _scrub(title)
        body = _scrub(body)
        fd, name = tempfile.mkstemp(prefix="body-", dir=self.cfg.tmp)
        os.close(fd)
        path = Path(name)
        try:
            path.write_text(body, encoding="utf-8")
            os.chmod(path, 0o600)
            proc = self._gh([*args, "--title", title, "--body-file", str(path)], stdin=None)
        finally:
            path.unlink(missing_ok=True)
        url = proc.stdout.strip()
        if url and not url.startswith(f"https://github.com/{RADON_REPO}/pull/"):
            url = ""
        return {"url": url}

    def _gh(self, args: list[str], stdin: str | None) -> subprocess.CompletedProcess[str]:
        token = _read_line(self.cfg.secrets / "github-token", os.geteuid(), self.cfg.secrets)
        env = child_env(self.cfg, {"GH_TOKEN": token, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1"})
        proc = self._exec([self.cfg.gh, *args], env=env, timeout=60, stdin=stdin)
        if proc.returncode != 0:
            raise Rejected("pr_failed")
        return proc

    def publish_report(self, caller: str, phase: str, day: str, path: str) -> dict:
        raw = read_confined([self.cfg.scratch_root(caller)], Path(path), self.cfg.client_uid, MAX_REPORT)
        try:
            text = _scrub(raw.decode("utf-8"))
        except UnicodeError as exc:
            raise Rejected("report") from exc
        rel = f"reports/{caller}/{day}/{phase}.md"
        work = Path(tempfile.mkdtemp(prefix="report-", dir=self.cfg.tmp))
        try:
            hosts = work / "known_hosts"
            hosts.write_text(GITHUB_HOSTKEY + "\n", encoding="utf-8")
            os.chmod(hosts, 0o600)
            key = shell_path(self.cfg.secrets / "reports-deploy-key")
            hosts_path = shell_path(hosts)
            ssh_bin = shell_path(Path(self.cfg.ssh))
            _read_bytes(Path(key), os.geteuid(), self.cfg.secrets)
            ssh = (
                f"{ssh_bin} -i {key} -o IdentitiesOnly=yes -o IdentityAgent=none "
                f"-o UserKnownHostsFile={hosts_path} -o StrictHostKeyChecking=yes "
                f"-o ConnectTimeout=20 -o BatchMode=yes -o ForwardAgent=no "
                f"-o ClearAllForwardings=yes -o UpdateHostKeys=no"
            )
            env = child_env(self.cfg, {"GIT_SSH_COMMAND": ssh})
            repo = work / "repo"
            clone = self._exec(
                [self.cfg.git, *GIT_LOCK, "clone", "--quiet", "--depth", "1", REPORTS_REMOTE, str(repo)],
                env=env,
                timeout=120,
            )
            if clone.returncode != 0:
                raise Rejected("report_failed")
            dest = repo / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            for argv in (
                [self.cfg.git, *GIT_LOCK, "-C", str(repo), "add", "--", rel],
                [
                    self.cfg.git,
                    *GIT_LOCK,
                    "-C",
                    str(repo),
                    "-c",
                    "user.name=radon-publisher",
                    "-c",
                    "user.email=radon-publisher@users.noreply.github.com",
                    "commit",
                    "--quiet",
                    "-m",
                    f"{caller} {phase} {day}",
                ],
                report_push_command(self.cfg.git, str(repo)),
            ):
                proc = self._exec(argv, env=env, timeout=120)
                if proc.returncode != 0:
                    raise Rejected("report_failed")
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return {"report": f"{REPORTS_WEB}/{rel}"}

    def notify(self, caller: str, title: str, message: str, url: str, url_title: str) -> dict:
        del caller
        token = _read_line(self.cfg.secrets / "pushover-token", os.geteuid(), self.cfg.secrets)
        user = _read_line(self.cfg.secrets / "pushover-user", os.geteuid(), self.cfg.secrets)
        fields = [("token", token), ("user", user), ("title", title), ("message", message)]
        if url:
            fields.append(("url", url))
        if url_title:
            fields.append(("url_title", url_title))
        stdin = "".join(f'form-string = "{key}={_curl_escape(value)}"\n' for key, value in fields)
        proc = self._exec(
            [
                self.cfg.curl,
                "-q",
                "-sS",
                "-m",
                "20",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "--config",
                "-",
                PUSHOVER_URL,
            ],
            env=child_env(self.cfg),
            timeout=30,
            stdin=stdin,
        )
        if proc.returncode != 0 or proc.stdout.strip() != "200":
            raise Rejected("notify_failed")
        return {"notified": True}


def shell_path(path: Path) -> str:
    """A path that can sit unquoted inside GIT_SSH_COMMAND."""
    text = str(path)
    if re.fullmatch(r"/[A-Za-z0-9._/-]+", text) is None:
        raise Rejected("secret_path")
    return text


def _curl_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")


def _secret_fd(path: Path, owner: int, root: Path) -> int:
    if path.parts[: len(root.parts)] != root.parts or any(part in (".", "..") for part in path.parts):
        raise Rejected("secret_path")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise Rejected("secret_missing") from exc
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != owner
        or info.st_mode & 0o077
        or info.st_size <= 0
        or info.st_size > MAX_SECRET
    ):
        os.close(fd)
        raise Rejected("secret_perms")
    return fd


def _read_bytes(path: Path, owner: int, root: Path) -> bytes:
    fd = _secret_fd(path, owner, root)
    try:
        return os.read(fd, MAX_SECRET)
    finally:
        os.close(fd)


def _read_line(path: Path, owner: int, root: Path) -> str:
    raw = _read_bytes(path, owner, root)
    try:
        text = raw.decode("utf-8").strip()
    except UnicodeError as exc:
        raise Rejected("secret_value") from exc
    if _SECRET_LINE.fullmatch(text) is None:
        raise Rejected("secret_value")
    return text


def dispatch(data: dict, cfg: Config, ops: object) -> dict:
    req = validate(data)
    op = req["op"]
    if op == "push":
        read_confined(
            [cfg.work_root(req["caller"]), cfg.scratch_root(req["caller"])],
            Path(req["bundle"]),
            cfg.client_uid,
            MAX_BUNDLE,
        )
        return ops.push(req["caller"], req["branch"], req["bundle"])  # type: ignore[attr-defined]
    if op == "draft_pr":
        if req["action"] == "open":
            return ops.open_draft(req["caller"], req["branch"], req["title"], req["body"])  # type: ignore[attr-defined]
        return ops.edit_draft(req["caller"], req["branch"], req["pr"], req["title"], req["body"])  # type: ignore[attr-defined]
    if op == "publish_report":
        read_confined([cfg.scratch_root(req["caller"])], Path(req["path"]), cfg.client_uid, MAX_REPORT)
        return ops.publish_report(req["caller"], req["phase"], req["date"], req["path"])  # type: ignore[attr-defined]
    return ops.notify(req["caller"], req["title"], req["message"], req["url"], req["url_title"])  # type: ignore[attr-defined]


def handle_payload(raw: bytes, *, peer: int, cfg: Config, ops: object) -> dict:
    op: object = "-"
    caller: object = "-"
    branch: object = "-"
    result = "rejected"
    reason = "internal"
    try:
        if peer != cfg.client_uid:
            reason = "peer"
            raise Rejected("peer")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            reason = "json"
            raise Rejected("json") from exc
        if isinstance(data, dict):
            op = data.get("op", "-")
            caller = data.get("caller", "-")
            branch = data.get("branch", "-")
        response = dispatch(data, cfg, ops)
        result = "ok"
        reason = "ok"
        return {"ok": True, **response}
    except Rejected as exc:
        result = "rejected"
        reason = exc.code
        return {"ok": False, "error": exc.code}
    except Exception:  # noqa: BLE001 - a malformed shape must never end the serving loop
        result = "rejected"
        reason = "internal"
        return {"ok": False, "error": "internal"}
    finally:
        try:
            log_request(cfg.log_path, peer=peer, op=op, caller=caller, branch=branch, result=result, reason=reason)
        except OSError:
            pass


def serve_once(cfg: Config, ops: object, *, timeout: float = 5.0) -> None:
    if cfg.socket_path.exists() or cfg.socket_path.is_symlink():
        if cfg.socket_path.is_symlink() or not stat.S_ISSOCK(cfg.socket_path.lstat().st_mode):
            raise Rejected("socket")
        cfg.socket_path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(cfg.socket_path))
        os.chmod(cfg.socket_path, 0o660)
        sock.listen(1)
        sock.settimeout(timeout)
        conn, _addr = sock.accept()
        try:
            conn.settimeout(timeout)
            _answer(conn, cfg, ops)
        finally:
            conn.close()
    finally:
        sock.close()


def _answer(conn: socket.socket, cfg: Config, ops: object) -> None:
    try:
        raw = _recv_all(conn, deadline=conn.gettimeout() or CONNECTION_TIMEOUT)
    except Rejected as exc:
        conn.sendall(json.dumps({"ok": False, "error": exc.code}).encode() + b"\n")
        return
    try:
        uid = peer_uid(conn)
    except (OSError, Rejected):
        uid = -1
    response = handle_payload(raw, peer=uid, cfg=cfg, ops=ops)
    conn.sendall(json.dumps(response).encode() + b"\n")


def serve(cfg: Config, ops: object, *, timeout: float = CONNECTION_TIMEOUT) -> None:
    """One listening socket for the life of the daemon. Callers never see a gap between requests.

    Each accepted connection is bounded by ``timeout``: a client that connects
    and sends nothing would otherwise hold every loop's push, draft and notify
    behind it for as long as it stayed open.
    """
    os.umask(0o077)
    cfg.tmp.mkdir(parents=True, exist_ok=True)
    cfg.log_path.parent.mkdir(parents=True, exist_ok=True)
    if cfg.socket_path.exists() or cfg.socket_path.is_symlink():
        if cfg.socket_path.is_symlink() or not stat.S_ISSOCK(cfg.socket_path.lstat().st_mode):
            raise SystemExit(1)
        cfg.socket_path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(cfg.socket_path))
        os.chmod(cfg.socket_path, 0o660)
        sock.listen(16)
        while True:
            try:
                conn, _addr = sock.accept()
            except OSError:
                continue
            try:
                conn.settimeout(timeout)
                _answer(conn, cfg, ops)
            except OSError:
                pass
            finally:
                conn.close()
    finally:
        sock.close()


def credential_fill(stdin: str, secrets: Path) -> str:
    fields = {}
    for line in stdin.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    if fields.get("protocol") != "https" or fields.get("host") != "github.com":
        return ""
    token = _read_line(secrets / "github-token", os.geteuid(), secrets)
    return f"protocol=https\nhost=github.com\nusername=x-access-token\npassword={token}\n"


def _config_from_args(args: argparse.Namespace) -> Config:
    return Config(
        client_uid=int(args.client_uid),
        bot_home=Path(args.bot_home),
        secrets=Path(args.secrets),
        socket_path=Path(args.socket),
        log_path=Path(args.log),
        tmp=Path(args.tmp),
        gitconfig=Path(args.gitconfig),
        git=args.git,
        gh=args.gh,
        curl=args.curl,
        ssh=args.ssh,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="publisher")
    sub = parser.add_subparsers(dest="cmd", required=True)
    gate = sub.add_parser("gate")
    gate.add_argument("--flag", required=True)
    serve_p = sub.add_parser("serve")
    for name, default in (
        ("--socket", DEFAULT_SOCKET),
        ("--secrets", DEFAULT_SECRETS),
        ("--log", DEFAULT_LOG),
        ("--tmp", DEFAULT_TMP),
        ("--gitconfig", "/usr/local/radon-runner/publisher-gitconfig"),
        ("--bot-home", "/Users/_radonbot"),
        ("--git", "/usr/bin/git"),
        ("--gh", "/opt/homebrew/bin/gh"),
        ("--curl", "/usr/bin/curl"),
        ("--ssh", "/usr/bin/ssh"),
    ):
        serve_p.add_argument(name, default=default)
    serve_p.add_argument("--client-uid", required=True)
    sub.add_parser("credential")
    args = parser.parse_args(argv)
    if args.cmd == "gate":
        ok, reason = flag_enabled(Path(args.flag))
        if not ok:
            print(f"publisher: disabled ({reason})", file=sys.stderr)
            return 1
        return 0
    if args.cmd == "credential":
        secrets = Path(os.environ.get("RADON_PUBLISHER_SECRETS", DEFAULT_SECRETS))
        try:
            sys.stdout.write(credential_fill(sys.stdin.read(), secrets))
        except Rejected:
            print("publisher: credential refused", file=sys.stderr)
            return 1
        return 0
    cfg = _config_from_args(args)
    os.environ["RADON_PUBLISHER_SECRETS"] = str(cfg.secrets)
    serve(cfg, RealOps(cfg))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
