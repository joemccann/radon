#!/usr/bin/env python3
"""Run-log daemon for the Mac runner.

The LaunchDaemon is installed only when a root-owned enable flag exists.
This process runs as _radonlog, owns the log directory, scrubs secrets
before anything is stored, and pushes that store to the private repo named
in the root-owned config. The repo name is configuration. The forge host
is fixed so the deploy key cannot be pointed at another host.
"""
from __future__ import annotations

import os
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

FLAG_NAME = "enable-runner-logd"
LOG_USER = "_radonlog"
LOOP_RE = re.compile(r"[a-z][a-z0-9-]{0,40}")
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\/[A-Za-z0-9][A-Za-z0-9_.-]*")
FORGE_REMOTE = "git@github.com:{repo}.git"
MAX_DATAGRAM = 8192
_SECRET_NAME = re.compile(
    r"(?i)^(?:GH_TOKEN|GITHUB_TOKEN|PUSHOVER_USER|PUSHOVER_TOKEN|UW_TOKEN)$"
    r"|(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY|CREDENTIAL)$"
)
# The name is any whole word (anchored at its start, so a long word is
# scanned once, not once per start and per split); whether it names a
# credential is decided on the matched word. The earlier shape put the
# keyword alternation between two unbounded runs and grew cubically.
_ASSIGN = re.compile(
    r"(?<![A-Za-z0-9_])(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?P<sep>[ \t]*[=:][ \t]*)"
    r"(?P<val>\"[^\"\n]*\"|'[^'\n]*'|\S+)"
)
_ASSIGN_NAME = re.compile(
    r"(?i)PUSHOVER_USER$|TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY|CREDENTIAL"
)
_FALLBACK_SHAPES = (
    (re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----.*", re.S), "[redacted-private-key]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "[redacted-key]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "[redacted-key]"),
    (re.compile(r"\bbearer\s+\S+", re.I), "bearer [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"), "[redacted-jwt]"),
)
_scrubber = None


def flag_root_uid() -> int:
    """Root uid the flag must have. A non-root test may name its own uid."""
    if os.geteuid() == 0:
        return 0
    raw = os.environ.get("RADON_RUNNER_LOGD_EXPECT_UID", "")
    if raw.isdigit():
        return int(raw)
    return 0


def flag_enabled(path: Path, root_uid: int | None = None) -> bool:
    """True only for a regular root-owned file the bot cannot write."""
    if root_uid is None:
        root_uid = flag_root_uid()
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return False
    if info.st_uid != root_uid:
        return False
    if info.st_mode & 0o022:
        return False
    return True


def _load_scrubber():
    global _scrubber
    if _scrubber is not None:
        return _scrubber
    try:
        from credential_redaction import scrub_credential_text
    except ImportError:
        here = Path(__file__).resolve().parent
        for extra in (here / "lib", here.parent):
            if (extra / "credential_redaction.py").is_file():
                sys.path.insert(0, str(extra))
        try:
            from credential_redaction import scrub_credential_text
        except ImportError:
            scrub_credential_text = None
    _scrubber = scrub_credential_text
    return _scrubber


def _fallback_shapes(text: str) -> str:
    for pattern, repl in _FALLBACK_SHAPES:
        text = pattern.sub(repl, text)
    return text


def _redact_named_assignments(text: str) -> str:
    def repl(m: re.Match) -> str:
        if _ASSIGN_NAME.search(m.group("name")) is None:
            return m.group(0)
        return m.group("name") + m.group("sep") + "[redacted]"

    return _ASSIGN.sub(repl, text)


def _redact_env_values(text: str, env: dict) -> str:
    values = []
    for name, value in env.items():
        if not value or len(value) < 8 or any(ch.isspace() for ch in value):
            continue
        if not _SECRET_NAME.search(name):
            continue
        values.append(value)
    for value in sorted(set(values), key=len, reverse=True):
        if value in text:
            text = text.replace(value, "[redacted]")
    return text


def scrub_line(text: str, env: dict | None = None) -> str:
    """Drop credential shapes, secret assignments, and known secret values."""
    text = text.replace("\x00", "")
    if env:
        text = _redact_env_values(text, env)
    text = _redact_named_assignments(text)
    scrub = _load_scrubber()
    if scrub is not None:
        text = scrub(text)
    else:
        text = _fallback_shapes(text)
    return text


def _private_file(path: Path, *, allow_root: bool = False) -> bool:
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        return False
    if info.st_mode & 0o077:
        return False
    if info.st_size <= 0:
        return False
    allowed = {os.geteuid()}
    if allow_root:
        allowed.add(0)
    return info.st_uid in allowed


def repo_ok(name: str) -> bool:
    """Owner/name only. Dots cannot walk out of the configured repo."""
    return bool(REPO_RE.fullmatch(name)) and ".." not in name


def read_config(path: Path) -> dict:
    info = os.lstat(path)
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ValueError("config")
    if info.st_mode & 0o022:
        raise ValueError("config")
    if info.st_uid not in (0, os.geteuid()):
        raise ValueError("config")
    data = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip()
    repo = data.get("repo", "")
    if not repo_ok(repo):
        raise ValueError("repo")
    try:
        interval = int(data.get("interval_secs", "900"))
    except ValueError:
        interval = 900
    interval = min(86400, max(60, interval))
    return {"repo": repo, "interval": interval, "remote": FORGE_REMOTE.format(repo=repo)}


def ingest_payload(payload: bytes, root: Path, env: dict | None = None) -> None:
    """Append one scrubbed line. A bad loop name is dropped."""
    if len(payload) > MAX_DATAGRAM:
        payload = payload[:MAX_DATAGRAM]
    text = payload.decode("utf-8", "replace").rstrip("\n")
    loop, sep, line = text.partition("\t")
    if not sep or not LOOP_RE.fullmatch(loop) or not line.strip():
        return
    # One datagram is one stored line: a line break inside the payload would
    # otherwise append entries nobody emitted, with any timestamp text.
    line = " ".join(line.splitlines())
    line = scrub_line(line, env)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest_dir = root / "repo" / "logs" / loop
    dest_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    dest = dest_dir / f"{day}.log"
    if dest.is_symlink() or dest_dir.is_symlink():
        return
    with dest.open("a", encoding="utf-8") as handle:
        handle.write(line.rstrip("\n") + "\n")
    os.chmod(dest, 0o600)


def ssh_command(root: Path) -> str:
    key = root / "keys" / "id_ed25519"
    known = root / "keys" / "known_hosts"
    return " ".join((
        "ssh",
        "-i", shlex.quote(str(key)),
        "-o", "IdentitiesOnly=yes",
        "-o", "IdentityAgent=none",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "UserKnownHostsFile=" + shlex.quote(str(known)),
    ))


def push_env(root: Path) -> dict:
    """A clean environment. Secret variables from the parent are not copied."""
    return {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(root / "repo"),
        "LANG": "C",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_SSH_COMMAND": ssh_command(root),
    }


def _git(repo: Path, git_bin: str, args: list[str], env: dict, run):
    cmd = [
        git_bin, "-C", str(repo),
        "-c", "safe.directory=" + str(repo),
        "-c", "user.name=radon-logd",
        "-c", "user.email=radon-logd@users.noreply.github.com",
        "-c", "core.hooksPath=/dev/null",
        *args,
    ]
    code, out = run(cmd, env)
    return code, out


def push_repo(root: Path, repo_name: str, *, run=None, git_bin: str = "/usr/bin/git") -> str:
    """Commit stored logs and push them. Returns pushed, skipped, or failed."""
    if not repo_ok(repo_name):
        return "skipped"
    key = root / "keys" / "id_ed25519"
    known = root / "keys" / "known_hosts"
    if not _private_file(key) or not _private_file(known):
        return "skipped"
    logs = root / "repo" / "logs"
    if not logs.is_dir() or logs.is_symlink() or not any(logs.rglob("*.log")):
        return "skipped"
    for item in logs.rglob("*"):
        if item.is_symlink():
            return "skipped"
    if run is None:
        def run(cmd, env):
            result = subprocess.run(
                cmd, env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=60)
            return result.returncode, result.stdout.decode("utf-8", "replace")
    env = push_env(root)
    repo = root / "repo"
    remote = FORGE_REMOTE.format(repo=repo_name)
    if not (repo / ".git").is_dir():
        if _git(repo, git_bin, ["init", "-b", "main"], env, run)[0] != 0:
            return "failed"
    code, url = _git(repo, git_bin, ["remote", "get-url", "origin"], env, run)
    if code != 0:
        if _git(repo, git_bin, ["remote", "add", "origin", remote], env, run)[0] != 0:
            return "failed"
    elif url.strip() != remote:
        return "skipped"
    if _git(repo, git_bin, ["add", "--", "logs"], env, run)[0] != 0:
        return "failed"
    diff, _out = _git(repo, git_bin, ["diff", "--cached", "--quiet"], env, run)
    if diff == 0:
        return "skipped"
    if diff != 1:
        return "failed"
    if _git(repo, git_bin, ["commit", "-m", "runner logs"], env, run)[0] != 0:
        return "failed"
    _git(repo, git_bin, ["pull", "--rebase", "--no-tags", "origin", "main"], env, run)
    if _git(repo, git_bin, ["push", "--quiet", "origin", "HEAD:main"], env, run)[0] != 0:
        return "failed"
    return "pushed"


def bind_ingest_socket(path: Path) -> socket.socket:
    if path.is_symlink():
        raise OSError("socket path is a symlink")
    if path.exists():
        if not stat.S_ISSOCK(path.lstat().st_mode):
            raise OSError("socket path is not a socket")
        path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(path))
    os.chmod(path, 0o660)
    return sock


def _send(sock: socket.socket, socket_path: str, loop: str, line: str, env: dict) -> bool:
    """True when the daemon accepted or only its buffer was full."""
    payload = (loop + "\t" + scrub_line(line, env)).encode("utf-8", "replace")[:MAX_DATAGRAM]
    try:
        sock.sendto(payload, socket_path)
    except BlockingIOError:
        return True
    except OSError:
        return False
    return True


_BLOCK_BEGIN = re.compile(r"-----BEGIN [A-Z0-9 ]+-----")
_BLOCK_END = re.compile(r"-----END [A-Z0-9 ]+-----")


class LineScrubber:
    """scrub_line per complete line, with the key-block state carried across lines.

    readline on a growing file returns whatever is written so far, so a
    fragment without its newline is held until the line completes (flush
    releases it at stop). A key printed one line at a time never shows BEGIN
    and END to the same scrub call: inside a block every line is dropped
    until its END.
    """

    # Enough for a block body padded with blank lines. While the context is
    # still absorbing a value, its header is pinned as the oldest line so a
    # body longer than the window cannot outlive the header that hides it;
    # the pinned context is bounded in bytes by dropping its oldest body
    # lines, never the header.
    CONTEXT_LINES = 64
    CONTEXT_BYTES = 256 * 1024

    def __init__(self, env: dict) -> None:
        self.env = env
        self.in_block = False
        self.pending = ""
        self.recent: list[str] = []
        self.absorbing = False

    def feed(self, fragment: str) -> str | None:
        self.pending += fragment
        if not self.pending.endswith("\n"):
            return None
        line, self.pending = self.pending.rstrip("\n"), ""
        return self._line(line)

    def flush(self) -> str | None:
        if not self.pending:
            return None
        line, self.pending = self.pending.rstrip("\n"), ""
        return self._line(line)

    def _line(self, line: str) -> str | None:
        if self.in_block:
            if _BLOCK_END.search(line):
                self.in_block = False
            return None
        if _BLOCK_BEGIN.search(line) and not _BLOCK_END.search(line):
            self.in_block = True
            return scrub_line(_BLOCK_BEGIN.sub("[redacted key block]", line), self.env)
        return self._with_context(line)

    def _with_context(self, line: str) -> str | None:
        """Scrub the line with the recent lines in front of it.

        The canonical rules consume a YAML block body or a multi-line quoted
        value only when they see its header. The line is what the context
        gains by appending it; a line the context absorbs (or that rewrites
        earlier output) is dropped.
        """
        scrub = _load_scrubber()
        before = "".join(self.recent)
        self.recent.append(line + "\n")
        self._trim()
        if scrub is None or not before:
            return scrub_line(line, self.env)
        prev = scrub(before)
        cur = scrub(before + line + "\n")
        delta = cur[len(prev):].strip("\n") if cur.startswith(prev) else ""
        absorbed = not delta or "\n" in delta
        if absorbed and not self.absorbing:
            # The line before this one opened the value: keep it as the
            # pinned header and let the context before it go.
            del self.recent[:-2]
        self.absorbing = absorbed
        if absorbed:
            return None
        return scrub_line(delta, self.env)

    def _trim(self) -> None:
        if not self.absorbing:
            del self.recent[: -self.CONTEXT_LINES]
            return
        if len(self.recent) > self.CONTEXT_LINES:
            del self.recent[1 : -(self.CONTEXT_LINES - 1)]
        while len(self.recent) > 2 and sum(map(len, self.recent)) > self.CONTEXT_BYTES:
            del self.recent[1]


def follow(file_path: Path, socket_path: str, loop: str) -> int:
    """Stream each new log line. A missing daemon is recorded and does not raise."""
    try:
        os.setpgrp()
    except OSError:
        pass
    stop = {"flag": False}

    def _stop(_signum, _frame):
        stop["flag"] = True

    signal.signal(signal.SIGUSR1, _stop)
    signal.signal(signal.SIGTERM, _stop)
    while not file_path.exists():
        if stop["flag"]:
            return 0
        time.sleep(0.05)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sent = False
    scrubber = LineScrubber(dict(os.environ))
    with file_path.open("r", encoding="utf-8", errors="replace") as handle:
        while True:
            line = handle.readline()
            if line:
                cleaned = scrubber.feed(line)
                if cleaned is None:
                    continue
                if not _send(sock, socket_path, loop, cleaned, dict(os.environ)):
                    return 1
                sent = True
                continue
            if stop["flag"]:
                tail = scrubber.flush()
                if tail is not None:
                    if not _send(sock, socket_path, loop, tail, dict(os.environ)):
                        return 1
                    sent = True
                if not sent and not _send(sock, socket_path, loop, "", dict(os.environ)):
                    return 1
                return 0
            time.sleep(0.05)


def _opt(argv: list[str], name: str) -> str:
    if name not in argv:
        raise SystemExit(2)
    value = argv[argv.index(name) + 1]
    if value.startswith("--"):
        raise SystemExit(2)
    return value


def serve(root: Path, config_path: Path) -> int:
    os.umask(0o077)
    sock = bind_ingest_socket(root / "ingest.sock")
    sock.settimeout(1.0)
    last = 0.0
    child = None
    while True:
        try:
            data, _addr = sock.recvfrom(MAX_DATAGRAM)
        except socket.timeout:
            data = b""
        if data:
            ingest_payload(data, root, env=None)
        if child is not None and child.poll() is not None:
            child = None
        now = time.monotonic()
        try:
            cfg = read_config(config_path)
        except (OSError, ValueError):
            cfg = None
        if cfg and child is None and now - last >= cfg["interval"]:
            child = subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).resolve()), "push",
                 "--root", str(root), "--config", str(config_path)],
                stdin=subprocess.DEVNULL, env=push_env(root),
            )
            last = now


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        return 2
    cmd = argv[1]
    if cmd == "flag-status":
        print("enabled" if flag_enabled(Path(_opt(argv, "--flag"))) else "disabled")
        return 0
    if cmd == "scrub":
        env = None if "--no-env" in argv else dict(os.environ)
        sys.stdout.write(scrub_line(sys.stdin.read(), env))
        return 0
    if cmd == "follow":
        return follow(Path(_opt(argv, "--file")), _opt(argv, "--socket"), _opt(argv, "--loop"))
    if cmd == "serve":
        return serve(Path(_opt(argv, "--root")), Path(_opt(argv, "--config")))
    if cmd == "push":
        cfg = read_config(Path(_opt(argv, "--config")))
        print(push_repo(Path(_opt(argv, "--root")), cfg["repo"]))
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except BrokenPipeError:
        raise SystemExit(0)
