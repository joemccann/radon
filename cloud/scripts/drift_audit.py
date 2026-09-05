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
  /usr/local/sbin/radon-deploy-root <- scripts/deploy-root-helper.sh
  /usr/local/sbin/radon-app-runtime <- scripts/radon-app-runtime.sh
  /usr/local/sbin/radon-docker-gw <- scripts/radon-docker-gw.sh
      all three installed by bootstrap-control-plane.sh / refresh-control-plane
  /etc/radon/ib-gateway-compose.yml <- git blob HEAD:cloud/docker-compose.yml
      (R-636 provenance: the working tree is radon-writable and is NOT the
      comparison basis; installed by install_docker_gw / refresh-control-plane)
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
  * live radon-* units absent from services/ -> drift. Retired
    radon-beta-* leftovers (the staging stack was never finished) stay
    "known-untracked" so a host that still has them does not redden
    config-drift. Do not recreate them. Remove them on the VPS.
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
from datetime import date, datetime, timezone
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
    # R-649: root-run helper surfaces installed by the control plane.
    (
        "/usr/local/sbin/radon-deploy-root",
        "scripts/deploy-root-helper.sh",
        "radon-deploy-root",
    ),
    (
        "/usr/local/sbin/radon-app-runtime",
        "scripts/radon-app-runtime.sh",
        "radon-app-runtime",
    ),
    (
        "/usr/local/sbin/radon-docker-gw",
        "scripts/radon-docker-gw.sh",
        "radon-docker-gw",
    ),
    # R-636: the installed compose body's canonical source is the git blob at
    # HEAD, never the radon-writable working tree (see the git: dispatch in
    # _compare_file_pair).
    (
        "/etc/radon/ib-gateway-compose.yml",
        "git:docker-compose.yml",
        "ib-gateway-compose",
    ),
]

GIT_BLOB_PREFIX = "git:"

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

    This process runs as root. The canonical file is 0640 root:radon, but the
    compatibility path lives under /home/radon, which the unprivileged account
    can replace, so the file's contents are attacker-influenced from root's
    point of view. Only the named keys are returned, and nothing here touches
    os.environ -- an appended LD_PRELOAD or PATH line is read past, not
    applied. Literal parsing (no shell) also keeps a `$VAR` in a secret from
    being expanded.
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


_ALLOWLIST_EXPIRY = re.compile(r"^expires=(\d{4}-\d{2}-\d{2})$")


def parse_allowlist(text: str) -> dict[str, dict]:
    """config/drift-allowlist.conf -> {drift-id: {"expires": date|None, "reason": str}}.

    Lines are `<drift-id> expires=YYYY-MM-DD <reason...>`; # comments and
    blanks are skipped. A missing or malformed expiry parses as None -- the
    ratchet in partition_allowlisted refuses to honor such an entry.
    """
    allow: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        drift_id, _, rest = line.partition(" ")
        rest = rest.strip()
        token, _, remainder = rest.partition(" ")
        expires = None
        reason = rest
        match = _ALLOWLIST_EXPIRY.match(token)
        if match:
            reason = remainder.strip()
            try:
                expires = date.fromisoformat(match.group(1))
            except ValueError:
                expires = None
        allow[drift_id] = {"expires": expires, "reason": reason}
    return allow


def partition_allowlisted(
    raw_drifts: list[dict], allowlist: dict[str, dict], today: date
) -> tuple[list[dict], dict[str, str]]:
    """R-058 ratchet: an allowlist entry is a bounded acknowledgment.

    * fresh entry (today <= expires) -> drift suppressed, reported as pending
    * expired entry -> the drift SURFACES, expiry named in the detail
    * entry without a valid expires= -> never honored
    * entry matching no observed drift -> stale-allowlist drift (remove it)
    """
    drifts: list[dict] = []
    allowed: dict[str, str] = {}
    matched: set[str] = set()
    for drift in raw_drifts:
        entry = allowlist.get(drift["id"])
        if entry is None:
            drifts.append(drift)
            continue
        matched.add(drift["id"])
        expires = entry["expires"]
        if expires is None:
            drifts.append(
                {
                    "id": drift["id"],
                    "detail": "allowlist entry has no expires=YYYY-MM-DD; not honored. "
                    + drift.get("detail", ""),
                }
            )
        elif today > expires:
            drifts.append(
                {
                    "id": drift["id"],
                    "detail": f"allowlist entry expired {expires.isoformat()}"
                    f" ({entry['reason']}); still pending. " + drift.get("detail", ""),
                }
            )
        else:
            allowed[drift["id"]] = f"until {expires.isoformat()}: {entry['reason']}"
    for drift_id in sorted(set(allowlist) - matched):
        drifts.append(
            {
                "id": f"stale-allowlist:{drift_id}",
                "detail": "allowlist entry matches no observed drift; remove it",
            }
        )
    return drifts, allowed


def classify_untracked_unit(name: str) -> str:
    """Retired radon-beta-* leftovers stay notes, not config-drift errors."""
    return "known-untracked" if name.startswith("radon-beta-") else "drift"


def classify_untracked_sudoers(name: str) -> str:
    """Same sunset rule for sudoers fragments as for units.

    2b1e7162 deleted `config/sudoers.d/radon-beta` but the live fragment
    survives on the host, so without this the beta sunset red-flags
    `config-drift` on every audit forever and the signal stops meaning
    anything. The fragment name has no `-` suffix, so the unit prefix
    (`radon-beta-`) does not match it.
    """
    return "known-untracked" if name.startswith("radon-beta") else "drift"


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
        note_bits.append("allowlisted-pending: " + ", ".join(sorted(allowed)))
    return {
        "summary": summary[:SUMMARY_CAP],
        "drift_count": len(drifts),
        "drifts": [
            {"id": d["id"], "detail": d.get("detail", "")[:DETAIL_CAP]}
            for d in drifts[:MAX_DRIFTS_IN_ROW]
        ],
        "allowed_count": len(allowed),
        "allowlisted_pending": sorted(allowed),
        "known_untracked": sorted(known_untracked),
        "note": "; ".join(note_bits)[:SUMMARY_CAP],
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


def _read_repo_blob(relative: str) -> str | None:
    """Read the canonical artifact from the git blob at HEAD (R-636).

    The installed ib-gateway-compose.yml is provisioned from the committed
    blob, so the working tree -- which the radon account can rewrite -- must
    never be the comparison basis for it.
    """
    try:
        proc = _run(
            [
                "git", "-c", f"safe.directory={GIT_REPO}", "-C", str(GIT_REPO),
                "show", f"HEAD:{(REPO.relative_to(GIT_REPO) / relative).as_posix()}",
            ]
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _compare_file_pair(live_path: str, repo_rel: str, label: str) -> dict | None:
    live = _read(Path(live_path))
    if repo_rel.startswith(GIT_BLOB_PREFIX):
        repo = _read_repo_blob(repo_rel[len(GIT_BLOB_PREFIX):])
    else:
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


def _repo_unit_counter(repo_path: Path) -> Counter:
    """Repo base unit merged with its OWN drop-ins, mirroring the live side.

    Comparing a merged live counter against the repo BASE alone made every
    setting a shipped drop-in adds — `User=root`, the `ExecStartPre=` reset,
    both `ExecStart=` lines — read as live-only, so all five app units went
    permanently `unit-mismatch` the moment the container drop-ins were
    installed, and a permanently-red `config-drift` buries every real drift.
    Not resolvable with an allowlist entry: the R-058 ratchet is a bounded
    acknowledgment, not a suppression for a permanent, intended state. R-392.
    """
    texts = [_read_repo(repo_path.relative_to(REPO)) or ""]
    dropin_dir = repo_path.with_name(repo_path.name + ".d")
    if dropin_dir.is_dir():
        for conf in sorted(dropin_dir.glob("*.conf")):
            texts.append(_read_repo(conf.relative_to(REPO)) or "")
    return merge_unit_counters(texts)


CANONICAL_ENV_FILE = Path("/etc/radon/env")
HOST_ROLES = frozenset({"app", "broker", "combined"})
# Units the control-plane refresh strips by role. Mirrors
# role_skips_control_plane_source() in scripts/deploy-root-helper.sh; their
# absence on that role is the intended state, not drift (REL-169, R-498).
ROLE_SKIPPED_UNITS: dict[str, frozenset[str]] = {
    "app": frozenset(
        {
            "radon-ib-gateway.service",
            "radon-ib-gateway-preheld-restart.service",
            "radon-ib-watchdog.service",
            "radon-ib-watchdog.timer",
            "radon-ib-gateway-remote.service",
        }
    ),
}
# Gateway runtime surfaces are absent by design on app-role hosts. Keep them
# visible as role-skipped notes without weakening broker/combined audits.
ROLE_SKIPPED_GATEWAY_SURFACES: dict[str, frozenset[str]] = {
    "app": frozenset(
        {"ib-gateway-control", "compose", "radon-docker-gw", "ib-gateway-compose"}
    ),
}


#: Roles that make this root-run auditor SKIP a check. R-604: the compat
#: `RADON_ENV_FILE` the unit points at lives under /home/radon, which the
#: unprivileged account can replace — `load_env_keys`' own docstring calls it
#: attacker-influenced from root's point of view. It may still NAME a role,
#: but it may not be the source of one that suppresses an audit surface.
SUPPRESSING_ROLES: frozenset[str] = frozenset(ROLE_SKIPPED_UNITS) | frozenset(
    ROLE_SKIPPED_GATEWAY_SURFACES
)


def resolve_host_role(environ=None) -> str:
    """RADON_HOST_ROLE: process env, then /etc/radon/env, then RADON_ENV_FILE.

    The last of those three is radon-writable, so a role it names is honoured
    only when that role does not suppress anything (R-604).
    """
    environ = os.environ if environ is None else environ
    raw = (environ.get("RADON_HOST_ROLE") or "").strip().strip("\"'")
    if not raw:
        raw = load_env_keys(
            CANONICAL_ENV_FILE, ("RADON_HOST_ROLE",)
        ).get("RADON_HOST_ROLE", "").strip()
    if not raw and environ.get("RADON_ENV_FILE"):
        compat = load_env_keys(
            Path(environ["RADON_ENV_FILE"]), ("RADON_HOST_ROLE",)
        ).get("RADON_HOST_ROLE", "").strip()
        if compat in SUPPRESSING_ROLES:
            print(
                f"[drift-audit] ignoring RADON_HOST_ROLE={compat!r} from the "
                "radon-writable compat env file; a suppressing role must come "
                "from the process environment or /etc/radon/env",
                file=sys.stderr,
            )
        else:
            raw = compat
    return raw if raw in HOST_ROLES else "combined"


def _check_units(drifts: list[dict], known_untracked: list[str]) -> None:
    role_skipped = ROLE_SKIPPED_UNITS.get(resolve_host_role(), frozenset())
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
            if name in role_skipped:
                known_untracked.append(f"role-skipped:{name}")
                continue
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
            _repo_unit_counter(repo_path),
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


def _check_sudoers(drifts: list[dict], known_untracked: list[str]) -> None:
    repo_dir = REPO / "config" / "sudoers.d"
    repo_frags = {p.name: p for p in repo_dir.glob("*") if p.is_file()}
    for name, repo_path in sorted(repo_frags.items()):
        drift = _compare_file_pair(
            str(SUDOERS_DIR / name), str(repo_path.relative_to(REPO)), f"sudoers/{name}"
        )
        if drift:
            drifts.append(drift)
    for live in sorted(SUDOERS_DIR.glob("radon*")):
        if live.name in repo_frags:
            continue
        if classify_untracked_sudoers(live.name) == "known-untracked":
            known_untracked.append(live.name)
        else:
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
    role_skipped = ROLE_SKIPPED_GATEWAY_SURFACES.get(
        resolve_host_role(), frozenset()
    )

    for live, repo_rel, label in FILE_PAIRS:
        if label in role_skipped:
            known_untracked.append(f"role-skipped:{label}")
            continue
        drift = _compare_file_pair(live, repo_rel, label)
        if drift:
            raw_drifts.append(drift)
    if "compose" in role_skipped:
        known_untracked.append("role-skipped:compose")
    else:
        _check_compose(raw_drifts)
    _check_units(raw_drifts, known_untracked)
    _check_sudoers(raw_drifts, known_untracked)
    _check_env_invariants(raw_drifts)
    # Do not conflate application checkout hygiene with deployed configuration
    # drift. Managed runtime artifacts above are compared byte-for-byte or by
    # normalized unit directives, which is both narrower and actionable.

    allowlist = parse_allowlist(_read_repo("config/drift-allowlist.conf") or "")
    drifts, allowed = partition_allowlisted(
        raw_drifts, allowlist, today=datetime.now(timezone.utc).date()
    )
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
