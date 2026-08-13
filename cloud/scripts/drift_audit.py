#!/usr/bin/env python3
"""Daily configuration-drift audit for the Radon VPS (DUR-06).

Production config is assembled from three sources that historically drift
apart: this repo's VPS working copy, hand-edited system files, and the live
container state. Drift caused/prolonged the 2026-06-09/10 gateway incidents
(autoheal sidecar absent, watchdog missing env). This audit turns drift into
a daily, banner-visible signal instead of an incident post-mortem.

This audit runs as root, so its own entrypoint may not live in the deploy
checkout that root would then execute. The canonical copy is installed
root-owned at /usr/local/lib/radon/drift_audit.py by the control-plane
bootstrap, and the checkout it compares against arrives as an explicit
argument (or RADON_CLOUD_ROOT), never derived from __file__.

Live file -> repo source of truth (install command, for when live is stale):

  /etc/caddy/Caddyfile <- caddy/Caddyfile
      sudo -n /usr/local/sbin/radon-deploy-root publish-caddy
      (validates the candidate, installs it atomically, then reloads caddy;
      the old raw `sudo cp` capability was retired -- cp follows a symlinked
      source and published unvalidated edge config)
  /usr/local/bin/radon <- scripts/operator-radon.sh
      sudo install -m 0755 scripts/operator-radon.sh /usr/local/bin/radon
  /usr/local/bin/radon-ib-gateway-control <- scripts/ib-gateway-control.sh
      installed atomically by setup-vps.sh:install_gateway_control
  /etc/polkit-1/rules.d/50-radon-services.rules <- config/polkit/50-radon-services.rules
      sudo install -m 0644 config/polkit/50-radon-services.rules /etc/polkit-1/rules.d/
  /etc/systemd/journald.conf.d/radon.conf <- services/journald-radon.conf
      sudo install -m 0644 services/journald-radon.conf /etc/systemd/journald.conf.d/radon.conf
  /etc/systemd/system/radon-.service.d/common.conf <- services/radon-.service.d/common.conf
      installed by setup-vps.sh:install_fleet_dropin
  /etc/sudoers.d/radon* <- config/sudoers.d/*
      sudo visudo -cf config/sudoers.d/NAME && sudo install -m 0440 config/sudoers.d/NAME /etc/sudoers.d/NAME
  docker-compose actually running the ib-gateway container <- docker-compose.yml
      resolved from the container's com.docker.compose.project.config_files label
  /etc/systemd/system/radon-*.{service,timer} <- services/
      sudo install -m 0644 services/NAME /etc/systemd/system/ && sudo systemctl daemon-reload

Unit files use an EFFECTIVE compare: the installed unit merged with its
per-unit drop-in dir (<unit>.d/*.conf), comments stripped, against the repo
unit file. This tolerates the DUR-02 pattern where StartLimit settings live
inline in the repo file but as a 50-start-limit.conf drop-in on the installed
copy. The fleet prefix drop-in (radon-.service.d/common.conf) is compared
separately as a plain file pair, not folded into every unit.

Also checked:
  * live radon-* units absent from services/ -> drift. radon-beta-* unit
    files are the deliberate exception: beta keep-vs-delete is an open
    operator decision, so they annotate an ok run as "known-untracked"
    instead of flipping the banner red (see config/drift-allowlist.conf
    for the same treatment of other acknowledged drift).
  * Environment invariant: every installed radon-*.service must carry
    RADON_DB_NO_REPLICA=1 (supplied fleet-wide by the prefix drop-in),
    asserted via `systemctl show -p Environment`.
  * Runtime artifacts are compared directly to their canonical cloud source.
    General Git worktree cleanliness is deliberately outside this audit: the
    production checkout can contain application/data hotfixes unrelated to
    deployed configuration, and every managed config target is already
    covered by an explicit file or unit comparison.

The result is written as service_health row `config-drift` -- heartbeat on
EVERY run (state=ok when clean, state=error with a compact diff summary on
any unallowed mismatch) -- over the stdlib libSQL HTTP pipeline API. Same
bounded-socket pattern as the main repo's scripts/health_service/turso_http.py;
never the libsql bindings, never an unbounded call.

This audit NEVER reads or compares .env* files. Secrets stay out of git and
out of this audit's output.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import stat
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_CLOUD_ROOT = Path("/home/radon/radon/cloud")
REPO = DEFAULT_CLOUD_ROOT
GIT_REPO = REPO.parent
SYSTEMD_DIR = Path("/etc/systemd/system")
SUDOERS_DIR = Path("/etc/sudoers.d")
SERVICE_NAME = "config-drift"
SUMMARY_CAP = 1500
DETAIL_CAP = 200
MAX_DRIFTS_IN_ROW = 10
SUBPROCESS_TIMEOUT = 10
TURSO_TIMEOUT = 10
HEALTH_WRITE_ATTEMPTS = 3

# (live absolute path, repo-relative path, drift label)
FILE_PAIRS = [
    ("/etc/caddy/Caddyfile", "caddy/Caddyfile", "caddyfile"),
    ("/usr/local/bin/radon", "scripts/operator-radon.sh", "operator-cli"),
    (
        "/usr/local/bin/radon-ib-gateway-control",
        "scripts/ib-gateway-control.sh",
        "ib-gateway-control",
    ),
    (
        "/etc/polkit-1/rules.d/50-radon-services.rules",
        "config/polkit/50-radon-services.rules",
        "polkit",
    ),
    (
        "/etc/systemd/journald.conf.d/radon.conf",
        "services/journald-radon.conf",
        "journald-dropin",
    ),
    (
        "/etc/systemd/system/radon-.service.d/common.conf",
        "services/radon-.service.d/common.conf",
        "fleet-dropin",
    ),
]

UNIT_GLOBS = ("radon-*.service", "radon-*.timer")

_UPSERT_SQL = (
    "INSERT INTO service_health (service, state, last_attempt_started_at, "
    "last_attempt_finished_at, last_error, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(service) DO UPDATE SET "
    "state = excluded.state, "
    "last_attempt_started_at = COALESCE(excluded.last_attempt_started_at, service_health.last_attempt_started_at), "
    "last_attempt_finished_at = COALESCE(excluded.last_attempt_finished_at, service_health.last_attempt_finished_at), "
    "last_error = excluded.last_error, "
    "updated_at = excluded.updated_at"
)


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/test_drift_audit.py)
# ---------------------------------------------------------------------------


def resolve_cloud_root(argv: list[str], environ) -> Path:
    """The cloud checkout this run compares the live system against.

    Kept separate from __file__ so the entrypoint can be installed root-owned
    outside the radon-writable checkout it audits.
    """
    candidate = argv[1] if len(argv) > 1 else environ.get("RADON_CLOUD_ROOT")
    root = Path(candidate) if candidate else DEFAULT_CLOUD_ROOT
    if root.is_symlink():
        raise RuntimeError(f"cloud root must not be a symlink: {root}")
    if candidate and not root.is_dir():
        raise RuntimeError(f"cloud root is not a directory: {root}")
    return root


def set_cloud_root(root: Path) -> None:
    global REPO, GIT_REPO
    REPO = root
    GIT_REPO = root.parent


def load_env_keys(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    """Read an allowlisted set of keys out of an env file, as DATA.

    This process runs as root while the file is 0600 radon:radon, so the file's
    contents are attacker-influenced from root's point of view. Only the named
    keys are returned, and nothing here touches os.environ -- an appended
    LD_PRELOAD or PATH line is read past, not applied. Literal parsing (no
    shell) also keeps a `$VAR` in a secret from being expanded.
    """
    values: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if not sep:
            continue
        key = key.strip()
        if key not in keys:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


def is_env_path(path: str) -> bool:
    """True for any .env* file -- the audit must never touch these."""
    return Path(path).name.startswith(".env")


def parse_unit_text(text: str) -> Counter:
    """Parse a systemd unit/drop-in into a multiset of (section, key, value).

    Comments (# / ;) and blank lines are ignored; trailing-backslash line
    continuations are joined. Returning a Counter keeps duplicate directives
    (After= twice, Environment= twice) honest in comparisons.
    """
    items: Counter = Counter()
    section = None
    pending = ""
    for raw in text.splitlines():
        line = (pending + raw.strip()).strip()
        pending = ""
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if line.endswith("\\"):
            pending = line[:-1].rstrip() + " "
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            items[(section, key.strip(), value.strip())] += 1
    return items


def merge_unit_counters(texts: list[str]) -> Counter:
    """Effective directive multiset for a unit: base file + drop-ins."""
    merged: Counter = Counter()
    for text in texts:
        merged += parse_unit_text(text)
    return merged


def unit_counter_diff(live: Counter, repo: Counter) -> str:
    """Empty string when effectively identical; otherwise a compact
    live-only / repo-only directive listing.

    Compares the SET of (section, key, value) directives, not the multiset:
    a unit reinstalled with DUR-02's inline StartLimit settings while the
    older 50-start-limit.conf drop-in still carries the identical values is
    duplicated-but-idempotent in systemd (last definition wins), not drift.
    Different VALUES for the same key still flag.
    """
    live_only = set(live) - set(repo)
    repo_only = set(repo) - set(live)
    if not live_only and not repo_only:
        return ""

    def fmt(items) -> str:
        return ", ".join(
            f"{key}={value}" for (_section, key, value) in sorted(items)
        )

    parts = []
    if live_only:
        parts.append(f"live-only: {fmt(live_only)}")
    if repo_only:
        parts.append(f"repo-only: {fmt(repo_only)}")
    return "; ".join(parts)


def parse_allowlist(text: str) -> dict[str, str]:
    """config/drift-allowlist.conf -> {drift-id: reason}. Lines are
    `<drift-id> <reason...>`; # comments and blanks are skipped."""
    allow: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        drift_id, _, reason = line.partition(" ")
        allow[drift_id] = reason.strip()
    return allow


def classify_untracked_unit(name: str) -> str:
    """radon-beta-* units are a known open operator decision (keep vs
    delete) -- note them instead of reddening the banner all weekend."""
    return "known-untracked" if name.startswith("radon-beta-") else "drift"


def build_last_error(
    drifts: list[dict], allowed: dict[str, str], known_untracked: list[str]
) -> dict:
    """Compact service_health last_error payload (capped, JSON-safe)."""
    if drifts:
        summary = "config drift: " + "; ".join(d["id"] for d in drifts)
    else:
        summary = "clean"
    note_bits = []
    if known_untracked:
        note_bits.append("known-untracked: " + ", ".join(sorted(known_untracked)))
    if allowed:
        note_bits.append(f"allowlisted drift: {len(allowed)}")
    return {
        "summary": summary[:SUMMARY_CAP],
        "drift_count": len(drifts),
        "drifts": [
            {"id": d["id"], "detail": d.get("detail", "")[:DETAIL_CAP]}
            for d in drifts[:MAX_DRIFTS_IN_ROW]
        ],
        "allowed_count": len(allowed),
        "known_untracked": sorted(known_untracked),
        "note": "; ".join(note_bits),
    }


# ---------------------------------------------------------------------------
# System-facing checks
# ---------------------------------------------------------------------------


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _read_repo(relative: str | Path) -> str | None:
    """Read a canonical checkout artifact without following any symlink.

    The audit runs as root against a radon-writable checkout. Descriptor-
    relative O_NOFOLLOW traversal closes both final-component and nested
    directory symlink escapes, including swaps between validation and read.
    """
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in ("", ".", "..") for part in parts):
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    directory_flags = flags | getattr(os, "O_DIRECTORY", 0)
    descriptors: list[int] = []
    try:
        current = os.open(REPO, directory_flags)
        descriptors.append(current)
        for component in parts[:-1]:
            current = os.open(component, directory_flags, dir_fd=current)
            descriptors.append(current)
        final = os.open(parts[-1], flags, dir_fd=current)
        descriptors.append(final)
        if not stat.S_ISREG(os.fstat(final).st_mode):
            return None
        with os.fdopen(os.dup(final), "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _line_delta(repo_text: str, live_text: str) -> str:
    delta = list(
        difflib.unified_diff(
            repo_text.splitlines(), live_text.splitlines(), lineterm=""
        )
    )
    added = sum(1 for l in delta if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in delta if l.startswith("-") and not l.startswith("---"))
    return f"live vs repo: +{added}/-{removed} lines"


def _compare_file_pair(live_path: str, repo_rel: str, label: str) -> dict | None:
    live = _read(Path(live_path))
    repo = _read_repo(repo_rel)
    if live is None and repo is None:
        return {"id": f"both-missing:{label}", "detail": f"{live_path} and {repo_rel}"}
    if live is None:
        return {"id": f"live-missing:{label}", "detail": live_path}
    if repo is None:
        return {"id": f"repo-missing:{label}", "detail": repo_rel}
    if live != repo:
        return {"id": f"file-mismatch:{label}", "detail": _line_delta(repo, live)}
    return None


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT
    )


def _check_compose(drifts: list[dict]) -> None:
    """Compare the compose file the ib-gateway container was ACTUALLY
    created from against the repo's docker-compose.yml."""
    try:
        proc = _run(
            [
                "docker",
                "inspect",
                "-f",
                '{{ index .Config.Labels "com.docker.compose.project.config_files" }}',
                "ib-gateway",
            ]
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        drifts.append({"id": "compose:unresolvable", "detail": str(exc)[:DETAIL_CAP]})
        return
    if proc.returncode != 0:
        drifts.append(
            {"id": "compose:unresolvable", "detail": proc.stderr.strip()[:DETAIL_CAP]}
        )
        return
    live_paths = [p for p in proc.stdout.strip().split(",") if p]
    if not live_paths:
        drifts.append({"id": "compose:unresolvable", "detail": "no config_files label"})
        return
    repo_compose = REPO / "docker-compose.yml"
    repo_text = _read_repo("docker-compose.yml")
    for live_path in live_paths:
        live_text = _read(Path(live_path))
        if live_text is None:
            drifts.append({"id": "compose:live-missing", "detail": live_path})
        elif repo_text is None:
            drifts.append({"id": "compose:repo-missing", "detail": str(repo_compose)})
        elif live_text != repo_text:
            drifts.append(
                {
                    "id": "compose:file-mismatch",
                    "detail": f"{live_path}: " + _line_delta(repo_text, live_text),
                }
            )


def _live_unit_counter(unit_path: Path) -> Counter:
    texts = [_read(unit_path) or ""]
    dropin_dir = unit_path.with_name(unit_path.name + ".d")
    if dropin_dir.is_dir():
        for conf in sorted(dropin_dir.glob("*.conf")):
            texts.append(_read(conf) or "")
    return merge_unit_counters(texts)


def _check_units(drifts: list[dict], known_untracked: list[str]) -> None:
    repo_units = {
        p.name: p
        for pattern in UNIT_GLOBS
        for p in (REPO / "services").glob(pattern)
        if p.is_file()
    }
    live_units = {
        p.name: p
        for pattern in UNIT_GLOBS
        for p in SYSTEMD_DIR.glob(pattern)
        if p.is_file()
    }

    for name, repo_path in sorted(repo_units.items()):
        live_path = live_units.get(name)
        if live_path is None:
            drifts.append({"id": f"not-installed:{name}", "detail": f"services/{name}"})
            continue
        if live_path.is_symlink():
            drifts.append(
                {
                    "id": f"symlink-unit:{name}",
                    "detail": f"{live_path} -> {os.readlink(live_path)}; install canonical regular file",
                }
            )
            continue
        detail = unit_counter_diff(
            _live_unit_counter(live_path),
            merge_unit_counters([_read_repo(repo_path.relative_to(REPO)) or ""]),
        )
        if detail:
            drifts.append({"id": f"unit-mismatch:{name}", "detail": detail})

        live_dropin = live_path.with_name(live_path.name + ".d")
        repo_dropin = repo_path.with_name(repo_path.name + ".d")
        for conf in sorted(live_dropin.glob("*.conf")) if live_dropin.is_dir() else []:
            counterpart = repo_dropin / conf.name
            if _read_repo(counterpart.relative_to(REPO)) is None:
                drifts.append(
                    {
                        "id": f"stale-dropin:{name}:{conf.name}",
                        "detail": f"remove {conf}; canonical base owns these directives",
                    }
                )

    for name in sorted(set(live_units) - set(repo_units)):
        if classify_untracked_unit(name) == "known-untracked":
            known_untracked.append(name)
        else:
            drifts.append(
                {"id": f"untracked-unit:{name}", "detail": str(live_units[name])}
            )


def _check_sudoers(drifts: list[dict]) -> None:
    repo_dir = REPO / "config" / "sudoers.d"
    repo_frags = {p.name: p for p in repo_dir.glob("*") if p.is_file()}
    for name, repo_path in sorted(repo_frags.items()):
        drift = _compare_file_pair(
            str(SUDOERS_DIR / name), str(repo_path.relative_to(REPO)), f"sudoers/{name}"
        )
        if drift:
            drifts.append(drift)
    for live in sorted(SUDOERS_DIR.glob("radon*")):
        if live.name not in repo_frags:
            drifts.append({"id": f"untracked-sudoers:{live.name}", "detail": str(live)})


def _check_env_invariants(drifts: list[dict]) -> None:
    """RADON_DB_NO_REPLICA=1 must be present on every installed
    radon-*.service (the fleet prefix drop-in supplies it)."""
    for unit_path in sorted(SYSTEMD_DIR.glob("radon-*.service")):
        if not unit_path.is_file():
            continue
        unit = unit_path.name
        try:
            proc = _run(["systemctl", "show", unit, "-p", "Environment", "--value"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            drifts.append(
                {"id": f"env-invariant:{unit}", "detail": str(exc)[:DETAIL_CAP]}
            )
            continue
        if "RADON_DB_NO_REPLICA=1" not in proc.stdout:
            drifts.append(
                {
                    "id": f"env-invariant:{unit}",
                    "detail": "RADON_DB_NO_REPLICA=1 missing from Environment",
                }
            )


def _check_repo_dirty(drifts: list[dict]) -> None:
    """Tracked-file modifications in the working copy = hand-edits that
    never made it into git. Untracked files (e.g. .env*) are ignored."""
    try:
        proc = _run(
            [
                "git", "-c", f"safe.directory={GIT_REPO}", "-C", str(GIT_REPO),
                "status", "--porcelain=v1", "--untracked-files=no", "--", "cloud",
            ]
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        drifts.append({"id": "repo-dirty:unresolvable", "detail": str(exc)[:DETAIL_CAP]})
        return
    if proc.returncode != 0:
        drifts.append(
            {"id": "repo-dirty:unresolvable", "detail": proc.stderr.strip()[:DETAIL_CAP]}
        )
        return
    for line in proc.stdout.splitlines():
        status, path = line[:2], line[3:].strip()
        if status == "??" or is_env_path(path):
            continue
        drifts.append({"id": f"repo-dirty:{path}", "detail": f"git status {status.strip()}"})


def gather() -> tuple[list[dict], dict[str, str], list[str]]:
    raw_drifts: list[dict] = []
    known_untracked: list[str] = []

    for live, repo_rel, label in FILE_PAIRS:
        drift = _compare_file_pair(live, repo_rel, label)
        if drift:
            raw_drifts.append(drift)
    _check_compose(raw_drifts)
    _check_units(raw_drifts, known_untracked)
    _check_sudoers(raw_drifts)
    _check_env_invariants(raw_drifts)
    # Do not conflate application checkout hygiene with deployed configuration
    # drift. Managed runtime artifacts above are compared byte-for-byte or by
    # normalized unit directives, which is both narrower and actionable.

    allowlist = parse_allowlist(_read_repo("config/drift-allowlist.conf") or "")
    drifts, allowed = [], {}
    for drift in raw_drifts:
        reason = allowlist.get(drift["id"])
        if reason is not None:
            allowed[drift["id"]] = reason
        else:
            drifts.append(drift)
    return drifts, allowed, known_untracked


# ---------------------------------------------------------------------------
# service_health write (stdlib libSQL HTTP pipeline -- bounded, no libsql)
# ---------------------------------------------------------------------------


def http_url_from_libsql(url: str) -> str:
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://"):]
    return url


DB_CREDENTIAL_KEYS = ("TURSO_DB_URL", "TURSO_AUTH_TOKEN")


def resolve_db_credentials(environ) -> dict[str, str]:
    """Turso credentials, preferring the process environment, then RADON_ENV_FILE.

    Under systemd this unit deliberately has no EnvironmentFile, so the keys
    arrive through the file read. A local operator run with the variables
    already exported keeps working unchanged.
    """
    resolved = {
        key: environ.get(key, "") for key in DB_CREDENTIAL_KEYS if environ.get(key)
    }
    missing = [key for key in DB_CREDENTIAL_KEYS if key not in resolved]
    env_file = environ.get("RADON_ENV_FILE")
    if missing and env_file:
        resolved.update(load_env_keys(Path(env_file), tuple(missing)))
    return resolved


def _hrana_arg(value: str | None) -> dict:
    return {"type": "null"} if value is None else {"type": "text", "value": value}


def write_service_health(
    state: str, last_error: dict | None, started_at: str
) -> None:
    credentials = resolve_db_credentials(os.environ)
    origin = http_url_from_libsql(credentials.get("TURSO_DB_URL", ""))
    token = credentials.get("TURSO_AUTH_TOKEN", "")
    if not origin or not token:
        raise RuntimeError("TURSO_DB_URL / TURSO_AUTH_TOKEN missing from environment")
    now = datetime.now(timezone.utc).isoformat()
    payload = json.dumps(
        {
            "requests": [
                {
                    "type": "execute",
                    "stmt": {
                        "sql": _UPSERT_SQL,
                        "args": [
                            _hrana_arg(SERVICE_NAME),
                            _hrana_arg(state),
                            _hrana_arg(started_at),
                            _hrana_arg(now),
                            _hrana_arg(json.dumps(last_error) if last_error else None),
                            _hrana_arg(now),
                        ],
                    },
                },
                {"type": "close"},
            ]
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        origin.rstrip("/") + "/v2/pipeline",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + token,
        },
    )
    with urllib.request.urlopen(req, timeout=TURSO_TIMEOUT) as resp:
        body = json.loads(resp.read(1_048_576).decode("utf-8"))
    first = body["results"][0]
    if first.get("type") != "ok":
        raise RuntimeError(f"service_health upsert rejected: {json.dumps(first)[:300]}")


def write_service_health_with_retry(
    state: str, last_error: dict | None, started_at: str
) -> None:
    """Retry bounded telemetry transport without changing the audit verdict."""
    last_exc: Exception | None = None
    for attempt in range(HEALTH_WRITE_ATTEMPTS):
        try:
            write_service_health(state, last_error, started_at)
            return
        except Exception as exc:  # noqa: BLE001 - preserve final transport detail
            last_exc = exc
            if attempt + 1 < HEALTH_WRITE_ATTEMPTS:
                time.sleep(attempt + 1)
    assert last_exc is not None
    raise last_exc


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    try:
        set_cloud_root(resolve_cloud_root(sys.argv, os.environ))
        drifts, allowed, known_untracked = gather()
    except Exception as exc:  # noqa: BLE001 - audit crash must still heartbeat
        crash = {"summary": f"audit crashed: {exc.__class__.__name__}: {exc}"[:SUMMARY_CAP]}
        print(crash["summary"], file=sys.stderr)
        try:
            write_service_health_with_retry("error", crash, started_at)
        except Exception as write_exc:  # noqa: BLE001
            print(f"service_health write failed: {write_exc}", file=sys.stderr)
        return 1

    state = "error" if drifts else "ok"
    last_error = build_last_error(drifts, allowed, known_untracked)

    print(f"config-drift audit: state={state}")
    for drift in drifts:
        print(f"  DRIFT  {drift['id']}  {drift.get('detail', '')}")
    for drift_id, reason in sorted(allowed.items()):
        print(f"  allow  {drift_id}  ({reason})")
    for name in sorted(known_untracked):
        print(f"  known-untracked  {name}")

    try:
        write_service_health_with_retry(state, last_error, started_at)
    except Exception as exc:  # noqa: BLE001 - bounded write, surface the failure
        print(f"service_health write failed: {exc}", file=sys.stderr)
        # The audit result is authoritative and was printed above. Telemetry
        # transport has its own watchdog/dead-man path; do not turn a clean
        # configuration into a failed systemd unit and recursive alert storm.
        return 1
    print("service_health row written: config-drift =", state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
