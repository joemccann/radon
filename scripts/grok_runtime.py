"""Track-latest Grok CLI/model. Incident runs always pass ``-m``.

Last-known-good is a machine-written state file owned by the daily
upgrader. There is no checked-in pin. ``--no-auto-update`` stays on every
incident invocation so only the upgrader may change the binary.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

Runner = Callable[..., object]

DEFAULT_LKG_PATH = Path(
    os.environ.get("RADON_GROK_LKG_PATH", "/var/lib/radon/grok_lkg.json")
)
DEFAULT_LOCK_PATH = Path(
    os.environ.get(
        "RADON_GROK_RUNTIME_LOCK", "/var/lib/radon/grok-runtime/grok-runtime.lock"
    )
)
DEFAULT_LIVE_BIN = Path(
    os.environ.get("GROK_BIN") or str(Path.home() / ".local" / "bin" / "grok")
)
REASONING_EFFORTS = frozenset({"low", "medium", "high"})
DEFAULT_REASONING = os.environ.get("GROK_REASONING_EFFORT", "high")
_VERSION_RE = re.compile(r"\b(\d+\.\d+\.\d+)\b")
_DEFAULT_MODEL_RE = re.compile(r"(?im)^default model:\s*(\S+)")
_MODEL_LINE_RE = re.compile(r"(?im)^[ \t]*([a-z0-9][a-z0-9._-]{2,})")
# The grok agent runs --always-approve over untrusted page text. Its parent
# holds Turso and Pushover credentials; the child gets an allowlist, never
# a scrub, so a new secret in the env file cannot leak by default.
GROK_CHILD_ENV_ALLOWLIST = (
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TMPDIR", "TZ",
    "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR",
    "HTTPS_PROXY", "HTTP_PROXY", "NO_PROXY", "https_proxy", "http_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "NODE_EXTRA_CA_CERTS",
    "GROK_HOME",
)
RUNTIME_STAMP_RE = re.compile(
    r"Ran\s+(\S+)\s+on\s+CLI\s+(\S+)", re.IGNORECASE
)


class GrokRuntimeError(ValueError):
    """CLI probe, LKG state, or lock is unusable."""


@dataclass(frozen=True)
class LkgState:
    cli_version: str
    binary_path: str
    model: str
    reasoning_effort: str
    promoted_at: str
    smoke_result: str

    def as_json(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ResolvedRuntime:
    model: str
    reasoning_effort: str
    cli_version: str
    binary_path: str
    used_fallback: bool
    warning: Optional[str]
    refused: bool
    source: str


def reasoning_effort() -> str:
    effort = (DEFAULT_REASONING or "high").strip().lower()
    if effort not in REASONING_EFFORTS:
        return "high"
    return effort


def parse_cli_version(stdout: str) -> str:
    match = _VERSION_RE.search(stdout or "")
    if not match:
        raise GrokRuntimeError("grok --version did not report x.y.z")
    return match.group(1)


def parse_models_listing(stdout: str) -> tuple[str | None, set[str]]:
    text = stdout or ""
    default = None
    match = _DEFAULT_MODEL_RE.search(text)
    if match:
        default = match.group(1).strip()
    available: set[str] = set()
    for line in text.splitlines():
        row = line.strip()
        if not row or row.lower().startswith("default model"):
            continue
        token = row.split()[0].strip("(),")
        if _MODEL_LINE_RE.fullmatch(token):
            available.add(token)
    if default:
        available.add(default)
    return default, available


def runtime_stamp(model: str, cli_version: str) -> str:
    return f"Ran {model} on CLI {cli_version}."


def extract_runtime_stamp(text: str) -> tuple[str, str] | None:
    match = RUNTIME_STAMP_RE.search(text or "")
    if not match:
        return None
    return match.group(1), match.group(2).rstrip(".")


def load_lkg(path: Path | None = None) -> LkgState | None:
    lkg_path = Path(path) if path else DEFAULT_LKG_PATH
    if not lkg_path.is_file():
        return None
    try:
        raw = json.loads(lkg_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise GrokRuntimeError(f"cannot read grok LKG {lkg_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise GrokRuntimeError("grok LKG must be a JSON object")
    required = (
        "cli_version",
        "binary_path",
        "model",
        "promoted_at",
        "smoke_result",
    )
    missing = [key for key in required if not str(raw.get(key) or "").strip()]
    if missing:
        raise GrokRuntimeError("grok LKG missing " + ", ".join(missing))
    effort = str(raw.get("reasoning_effort") or "high").strip().lower()
    if effort not in REASONING_EFFORTS:
        effort = "high"
    return LkgState(
        cli_version=str(raw["cli_version"]).strip(),
        binary_path=str(raw["binary_path"]).strip(),
        model=str(raw["model"]).strip(),
        reasoning_effort=effort,
        promoted_at=str(raw["promoted_at"]).strip(),
        smoke_result=str(raw["smoke_result"]).strip(),
    )


# An LKG binary runs `--always-approve` over untrusted page text. It must be
# a real executable the upgrader installed, never a path a test or the agent
# itself could have created. 2026-09-29: a pytest basetemp candidate was
# promoted over the live CLI (runbook grok-live-binary-relinked-by-pytest).
UNTRUSTED_BIN_DIRS = ("/tmp", "/var/tmp", "/dev/shm")
UNTRUSTED_PATH_MARKERS = ("pytest-of-",)


def _untrusted_roots() -> list[Path]:
    roots = {Path(p) for p in UNTRUSTED_BIN_DIRS}
    roots.add(Path(tempfile.gettempdir()))
    # Both spellings: /tmp is a symlink on macOS.
    return sorted(roots | {r.resolve() for r in roots})


def lkg_binary_problem(
    binary_path: str,
    *,
    extra_untrusted: Iterable[Path] = (),
) -> str | None:
    """Why ``binary_path`` must not be executed as last-known-good, or None.

    Refuses relative, missing, non-regular, non-executable, world-writable,
    foreign-owned, temp-dir and pytest-basetemp paths (checked on the path
    as written, its parent, and the resolved target).
    """
    raw = str(binary_path or "").strip()
    if not raw or not os.path.isabs(raw):
        return "not an absolute path"
    written = Path(raw)
    try:
        target = written.resolve(strict=True)
    except (OSError, RuntimeError):
        return "missing"
    candidates = (written, written.parent.resolve(), target)
    for path in candidates:
        if any(m in part for part in path.parts for m in UNTRUSTED_PATH_MARKERS):
            return "inside a pytest basetemp"
    roots = _untrusted_roots() + [Path(p).resolve() for p in extra_untrusted]
    for path in candidates:
        for root in roots:
            if path == root or path.is_relative_to(root):
                return f"under untrusted directory {root}"
    try:
        st = target.stat()
        parent_mode = target.parent.stat().st_mode
    except OSError:
        return "missing"
    if not stat.S_ISREG(st.st_mode):
        return "not a regular file"
    if not os.access(target, os.X_OK):
        return "not executable"
    if st.st_mode & stat.S_IWOTH or parent_mode & stat.S_IWOTH:
        return "world-writable"
    if st.st_uid not in {0, os.getuid()}:
        return "owned by another user"
    return None


def write_lkg(path: Path, state: LkgState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state.as_json(), indent=2) + "\n"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    tmp.replace(path)


def _run(runner: Runner, argv: list[str]) -> str:
    try:
        proc = runner(argv)
    except FileNotFoundError as exc:
        # No return code when GROK_BIN is absent. Same class as a nonzero
        # --version: the caller refuses or falls back to last-known-good.
        target = argv[0] if argv else "grok"
        raise GrokRuntimeError(f"{target} failed: {exc}") from exc
    if getattr(proc, "returncode", 1) != 0:
        raise GrokRuntimeError(
            f"{argv[0]} failed: {(getattr(proc, 'stderr', '') or '')[:200]}"
        )
    return getattr(proc, "stdout", "") or ""


def probe_cli(
    grok_bin: str,
    *,
    runner: Runner,
) -> tuple[str, str | None, set[str]]:
    version = parse_cli_version(_run(runner, [grok_bin, "--version"]))
    default, available = parse_models_listing(_run(runner, [grok_bin, "models"]))
    return version, default, available


def resolve_latest(
    *,
    grok_bin: str,
    runner: Runner,
    lkg: LkgState | None = None,
) -> ResolvedRuntime:
    """Resolve the live default model and always return an explicit ``-m`` id."""
    try:
        installed, default, available = probe_cli(grok_bin, runner=runner)
    except GrokRuntimeError as exc:
        if lkg is None:
            return ResolvedRuntime(
                model="",
                reasoning_effort=reasoning_effort(),
                cli_version="",
                binary_path=grok_bin,
                used_fallback=False,
                warning=f"grok probe failed ({exc}); no last-known-good",
                refused=True,
                source="none",
            )
        return ResolvedRuntime(
            model=lkg.model,
            reasoning_effort=lkg.reasoning_effort,
            cli_version=lkg.cli_version,
            binary_path=lkg.binary_path,
            used_fallback=True,
            warning=f"grok probe failed ({exc}); using last-known-good",
            refused=False,
            source="lkg",
        )
    if default and default in available:
        return ResolvedRuntime(
            model=default,
            reasoning_effort=reasoning_effort(),
            cli_version=installed,
            binary_path=grok_bin,
            used_fallback=False,
            warning=None,
            refused=False,
            source="models",
        )
    if lkg is not None and lkg.model:
        return ResolvedRuntime(
            model=lkg.model,
            reasoning_effort=lkg.reasoning_effort,
            cli_version=lkg.cli_version,
            binary_path=lkg.binary_path,
            used_fallback=True,
            warning="grok models had no default; using last-known-good",
            refused=False,
            source="lkg",
        )
    return ResolvedRuntime(
        model="",
        reasoning_effort=reasoning_effort(),
        cli_version=installed,
        binary_path=grok_bin,
        used_fallback=False,
        warning="grok models had no default; refusing implicit CLI default",
        refused=True,
        source="none",
    )


def should_retry_lkg(
    returncode: int,
    stdout: str,
    stderr: str,
) -> bool:
    """Retry LKG on non-zero, model-unavailable, or unparseable output."""
    if returncode != 0:
        return True
    blob = f"{stdout or ''}\n{stderr or ''}".lower()
    if "unavailab" in blob and "model" in blob:
        return True
    return "RESULT:" not in (stdout or "")


def runtime_from_lkg(lkg: LkgState) -> ResolvedRuntime:
    return ResolvedRuntime(
        model=lkg.model,
        reasoning_effort=lkg.reasoning_effort,
        cli_version=lkg.cli_version,
        binary_path=lkg.binary_path,
        used_fallback=True,
        warning=None,
        refused=False,
        source="fallback",
    )


@contextmanager
def exclusive_lock(
    path: Path | None = None,
    *,
    blocking: bool = True,
) -> Iterator[None]:
    """Serialize incident runs against a live binary promotion."""
    lock_path = Path(path) if path else DEFAULT_LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    flags = fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB
    try:
        fcntl.flock(fd, flags)
    except BlockingIOError as exc:
        os.close(fd)
        raise GrokRuntimeError(f"grok runtime lock busy: {lock_path}") from exc
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def grok_child_env(overrides: Optional[dict] = None) -> dict:
    """Allowlisted environment for every grok child process."""
    env = {k: os.environ[k] for k in GROK_CHILD_ENV_ALLOWLIST if os.environ.get(k)}
    env.update(overrides or {})
    return env


def _default_runner(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=kwargs.get("cwd"),
        env=kwargs.get("env") or grok_child_env(),
        capture_output=True,
        text=True,
        timeout=kwargs.get("timeout", 120),
        input=kwargs.get("input"),
    )
