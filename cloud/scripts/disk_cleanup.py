#!/usr/bin/env python3
"""Weekly reclaim of the VPS root filesystem (2026-08-27 P1).

The watchdog `root-disk-usage` check paged P1 when root hit 98% (1.7 GiB free
on a 75G disk). Four consumers had unbounded growth and no pruner anywhere in
cloud/ or scripts/:

  * per-SHA ``ghcr.io/joemccann/radon-{node,python}`` image pairs (~5.8G per
    deploy) pulled by radon-app-runtime -- nothing ever ran `docker image rm`;
  * ``stage.<sha>.XXXXXX`` / ``backup.<sha>.XXXXXX`` deploy worktrees under
    /home/radon/.radon-releases -- deploy.sh's cleanup_staged_release_path is
    best-effort and logs "leaving for root cleanup" when it gives up;
  * ~/.npm/_cacache (8.9G) and ~/.cache/pip (989M) -- pure caches;
  * journald (1.0G).

DELIBERATELY OUT OF SCOPE: /home/radon/radon-cloud/backups/db. Retention there
is db_backup.py's RETENTION_DAYS=30, it is real data with no offsite copy in
that script, and shrinking it is an operator policy call. It is in
PROTECTED_PATHS so no category here can reach it.

Every destructive decision is a PURE selector (tests/test_disk_cleanup.py), so
the retention rules are provable without a live docker daemon. The running
broker image (ghcr.io/gnzsnz/ib-gateway) and the autoheal sidecar are outside
MANAGED_IMAGE_REPOSITORIES and additionally named in
PROTECTED_IMAGE_REPOSITORIES -- the same belt-and-braces posture as
radon-app-runtime.sh's refuse_host_plane(). /home/radon/radon (the live tree,
the only non-detached entry in `git worktree list`) can never be selected as
an orphaned release.

This job runs as ROOT: the docker engine socket is root-only (radon is
deliberately not in group docker) and journald vacuuming is root-only. Root
therefore executes the control-plane copy at /usr/local/lib/radon/, never the
radon-writable checkout, and never runs `git` inside /home/radon/radon --
git reads repo-local config the radon account owns, which would turn a
`core.fsmonitor` line into a root shell. HEAD is resolved by READING
.git/HEAD, and stale worktree admin dirs are removed by filesystem ops
instead of `git worktree prune`.

Heartbeats the ``service_health`` row ``disk-cleanup`` on EVERY run -- ok with
per-category bytes, error with the failing categories -- because a cleanup
timer with no liveness signal is a silently-dead cleanup timer
(feedback_service_health_heartbeat). A category that finds nothing is a normal
outcome and never fails the unit; a category that RAISES flips the heartbeat
to error (visible on the dashboard + the watchdog daily bucket) while the
remaining categories still run and the unit still exits 0, the same shape as
drift_audit.py reporting drift.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable, NamedTuple, Optional

SERVICE_NAME = "disk-cleanup"
SUMMARY_CAP = 900
TURSO_TIMEOUT = 10
SUBPROCESS_TIMEOUT = 300

# Retention constants -- the only knobs an operator should need.
KEEP_IMAGE_PAIRS = 2
RELEASE_MAX_AGE_DAYS = 3
JOURNAL_VACUUM_SIZE = "200M"

DOCKER_BIN = "/usr/bin/docker"
JOURNALCTL_BIN = "/usr/bin/journalctl"

LIVE_TREE = Path("/home/radon/radon")
RELEASES_DIR = Path("/home/radon/.radon-releases")
WORKTREE_ADMIN_DIR = LIVE_TREE / ".git" / "worktrees"
# deploy.sh:119. A deploy that dies mid-transition leaves this pending, naming
# the stage and backup worktrees under RELEASES_DIR. The backup dir IS the
# rollback source `restore_release_backup` reads, and it is exactly the shape
# `select_prunable_releases` matches on. R-340.
TRANSITION_JOURNAL_FILE = Path("/home/radon/.radon-deploy-transition.json")

# radon-app-runtime.sh:39 sets RADON_APP_IMAGE_FALLBACK_TAG (default `latest`)
# and resolve_image falls back to it when the pinned SHA manifest probe fails.
# It is not a 40-hex SHA so it is never head_tag, and being old it is never in
# the newest pairs — so the sweep deleted the one copy that survives a GHCR
# outage. Under RADON_RUNTIME=host no container holds it, so the daemon does
# not refuse either. R-342.
DEFAULT_IMAGE_FALLBACK_TAG = "latest"

# Plausibility ceilings. A selector regression — a widened RELEASE_NAME_RE, a
# PROTECTED_PATHS typo — used to reach production as an unrecoverable rmtree on
# its first firing rather than as a refusal. R-368.
MAX_PRUNE_PATHS = 24
MAX_RECLAIM_BYTES = 64 * 1024**3

# Set by --dry-run: every selector still runs and every size is still
# reported, but nothing is deleted. R-368.
DRY_RUN = False


class ReclaimCeilingExceeded(RuntimeError):
    """A category's computed target set is implausibly large; refuse it."""


# deploy.sh:103. `cleanup_caches` unconditionally rmtree's ~/.npm/_cacache and
# ~/.cache/pip with NO age threshold, so a CI deploy in its dependency-build
# phase when this timer fires has those trees deleted underneath bun install /
# pip, producing a failed or silently corrupt build promoted into the release.
# The job took no lock of any kind. R-341.
DEPLOY_LOCK_FILE = Path("/home/radon/.radon-deploy.lock")
# Well under TimeoutStartSec=900, so a deploy that outlasts this skips the
# weekly reclaim rather than wedging the unit.
DEPLOY_LOCK_TIMEOUT_S = 600.0
DEPLOY_LOCK_POLL_S = 5.0
EX_TEMPFAIL = 75


@contextlib.contextmanager
def acquire_deploy_lock(path: Path = None, timeout_s: float = None):
    """Hold the production deploy lock, or yield False when a deploy owns it.

    O_NOFOLLOW: the lock file sits under /home/radon, which the radon account
    owns, so root must refuse to follow it anywhere. A missing lock file means
    no deploy has ever run on this host, which is not a reason to refuse the
    sweep.
    """
    lock_path = DEPLOY_LOCK_FILE if path is None else path
    budget = DEPLOY_LOCK_TIMEOUT_S if timeout_s is None else timeout_s
    try:
        fd = os.open(lock_path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        yield True
        return
    except OSError as exc:
        print(f"[disk-cleanup] refusing deploy lock {lock_path}: {exc}", file=sys.stderr)
        yield False
        return

    deadline = time.monotonic() + budget
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(DEPLOY_LOCK_POLL_S)
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def image_fallback_tag() -> str:
    return os.environ.get("RADON_APP_IMAGE_FALLBACK_TAG") or DEFAULT_IMAGE_FALLBACK_TAG

# Ours to prune: one pair per deployed SHA, pulled by radon-app-runtime.
MANAGED_IMAGE_REPOSITORIES = (
    "ghcr.io/joemccann/radon-node",
    "ghcr.io/joemccann/radon-python",
)
# Never ours: the live broker container and its autoheal sidecar. Redundant
# with the allowlist above on purpose (radon-app-runtime.sh:refuse_host_plane).
PROTECTED_IMAGE_REPOSITORIES = (
    "ghcr.io/gnzsnz/ib-gateway",
    "willfarrell/autoheal",
)

CACHE_TARGETS = (
    Path("/home/radon/.npm/_cacache"),
    Path("/home/radon/.cache/pip"),
)

# Deleting any of these breaks a running service or forces a slow re-download:
# ms-playwright is the browser radon-newsfeed drives, huggingface holds the
# Chronos-2 forecast weights, radon-wheels is the deploy wheel cache, and the
# db backup tree is real data governed by db_backup.py's own retention.
PROTECTED_PATHS = (
    LIVE_TREE,
    Path("/home/radon/radon-cloud/backups"),
    Path("/home/radon/.cache/ms-playwright"),
    Path("/home/radon/.cache/huggingface"),
    Path("/home/radon/.cache/fastembed"),
    Path("/home/radon/.cache/radon-wheels"),
)

# deploy.sh:RELEASES_DIR mktemp templates -- `stage.<sha12>.XXXXXX` and
# `backup.<sha12>.XXXXXX`. Anything else under the releases dir is left alone.
RELEASE_NAME_RE = re.compile(r"^(?:stage|backup)\.[0-9a-f]{7,40}\.[A-Za-z0-9]{6}$")

_SIZE_UNITS = {
    "b": 1,
    "kb": 1_000,
    "mb": 1_000_000,
    "gb": 1_000_000_000,
    "tb": 1_000_000_000_000,
}
_SIZE_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)\s*([kmgt]?b)$", re.IGNORECASE)
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class Image(NamedTuple):
    repository: str
    tag: str
    image_id: str
    created_at: float
    size_bytes: int


# ---------------------------------------------------------------------------
# Pure selectors and parsers (unit-tested in tests/test_disk_cleanup.py)
# ---------------------------------------------------------------------------


def parse_docker_size(text: str) -> int:
    """``4.69GB`` -> bytes. Docker reports decimal units."""
    match = _SIZE_RE.match((text or "").strip())
    if not match:
        return 0
    return int(float(match.group(1)) * _SIZE_UNITS[match.group(2).lower()])


def parse_reclaimed_space(output: str) -> int:
    """Bytes from `docker image prune`'s ``Total reclaimed space:`` line."""
    for line in (output or "").splitlines():
        _, sep, tail = line.partition("Total reclaimed space:")
        if sep:
            return parse_docker_size(tail.strip())
    return 0


def parse_docker_created(text: str) -> float:
    """``2026-08-20 03:11:22 +0000 UTC`` -> epoch seconds (0.0 if unparseable)."""
    cleaned = re.sub(r"\s+[A-Z]{2,5}$", "", (text or "").strip())
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S %z").timestamp()
    except ValueError:
        return 0.0


def parse_image_lines(output: str) -> list[Image]:
    """Tab-separated `docker image ls` rows; malformed rows are dropped."""
    images = []
    for line in (output or "").splitlines():
        fields = line.split("\t")
        if len(fields) != 5:
            continue
        repository, tag, image_id, created, size = (f.strip() for f in fields)
        images.append(
            Image(
                repository=repository,
                tag=tag,
                image_id=image_id,
                created_at=parse_docker_created(created),
                size_bytes=parse_docker_size(size),
            )
        )
    return images


def select_prunable_images(
    images: Iterable[Image], head_tag: str, keep_pairs: int = KEEP_IMAGE_PAIRS
) -> list[Image]:
    """Managed radon app images safe to remove.

    Keeps the pair matching the live checkout's HEAD plus the ``keep_pairs``
    newest pairs. An unresolvable HEAD prunes NOTHING: without it any tag
    could be the one the running containers were started from.
    """
    if not head_tag:
        return []
    managed = [
        image
        for image in images
        if image.repository in MANAGED_IMAGE_REPOSITORIES
        and image.repository not in PROTECTED_IMAGE_REPOSITORIES
        and image.tag
        and image.tag != "<none>"
    ]
    newest_per_tag: dict[str, float] = {}
    for image in managed:
        newest_per_tag[image.tag] = max(
            newest_per_tag.get(image.tag, image.created_at), image.created_at
        )
    ordered = sorted(newest_per_tag, key=lambda tag: (newest_per_tag[tag], tag), reverse=True)
    keep = set(ordered[:keep_pairs])
    keep.add(head_tag)
    keep.add(image_fallback_tag())
    return sorted(
        (image for image in managed if image.tag not in keep),
        key=lambda image: (image.repository, image.tag),
    )


def is_protected_path(path: Path) -> bool:
    """True for anything this job must never delete.

    A protected path's ANCESTORS are protected too: removing /home/radon/.cache
    would take ms-playwright and the Chronos weights with it.
    """
    # RESOLVED, both sides. Comparing unresolved Paths let an ANCESTOR symlink
    # defeat the check entirely: replacing /home/radon/.cache with a link to
    # /var/lib/radon made remove_tree(/home/radon/.cache/pip) compare clean and
    # root-rmtree /var/lib/radon/pip. The radon account owns /home/radon and is
    # the adversary this module's docstring names. R-343.
    candidate = _resolve(Path(path))
    for protected in PROTECTED_PATHS:
        resolved = _resolve(Path(protected))
        if candidate == resolved:
            return True
        if resolved in candidate.parents or candidate in resolved.parents:
            return True
    return False


def _resolve(path: Path) -> Path:
    """Fully-resolved path, falling back to the lexical form when absent."""
    try:
        return path.resolve()
    except OSError:
        return path


def has_symlink_component(path: Path) -> bool:
    """True when ANY component of ``path`` is a symlink, not just the leaf.

    `remove_tree` checked `path.is_symlink()` on the final component only, so
    a link one level up was invisible to it. R-343.
    """
    current = Path(path)
    seen: set[Path] = set()
    while current not in seen:
        seen.add(current)
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
        if current.parent == current:
            break
        current = current.parent
    return False


def select_prunable_releases(
    entries: Iterable[tuple[Path, float]],
    now_secs: float,
    max_age_days: int = RELEASE_MAX_AGE_DAYS,
    releases_dir: Path = RELEASES_DIR,
) -> list[Path]:
    """Leaked deploy worktrees older than the age threshold.

    ``entries`` is an iterable of (path, mtime_secs). A path is eligible only
    when it is a DIRECT child of the releases dir AND its name matches one of
    deploy.sh's two mktemp templates, so /home/radon/radon -- the live tree --
    is unreachable by construction as well as by is_protected_path.
    """
    live = live_transition_paths()
    if live is None:
        # A journal that exists but cannot be read means a deploy is
        # mid-flight and we cannot tell WHICH directories it owns. Refuse the
        # whole category rather than guess; a week of leaked worktrees costs
        # disk, deleting the rollback source costs the rollback. R-340.
        return []
    cutoff = now_secs - max_age_days * 86400
    selected = []
    for path, mtime in entries:
        candidate = Path(path)
        if candidate.parent != Path(releases_dir):
            continue
        if not RELEASE_NAME_RE.match(candidate.name):
            continue
        if is_protected_path(candidate):
            continue
        if str(candidate) in live:
            continue
        if mtime >= cutoff:
            continue
        selected.append(candidate)
    return sorted(selected)


def live_transition_paths() -> Optional[set[str]]:
    """Directories a pending deploy transition still owns.

    Empty set when no journal exists (the normal case). ``None`` when a
    journal exists but cannot be parsed — the caller must then refuse the
    whole category, because an unreadable journal still means a deploy is
    mid-flight. R-340.
    """
    try:
        raw = TRANSITION_JOURNAL_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return set()
    except OSError:
        return None
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    return {
        str(record[key])
        for key in ("stage_dir", "backup_dir")
        if record.get(key)
    }


def select_stale_worktree_admin_dirs(
    entries: Iterable[tuple[Path, bool]],
) -> list[Path]:
    """`.git/worktrees/<name>` admin dirs whose worktree no longer exists.

    ``entries`` is (admin_dir, worktree_still_exists). This is what
    `git worktree prune` would remove, done with filesystem ops so root never
    executes git inside a repository the radon account can configure.
    """
    return sorted(admin for admin, exists in entries if not exists)


def resolve_head_sha(git_dir: Path) -> str:
    """The live checkout's HEAD commit, read as DATA from .git.

    Never shells out to git: repo-local config is radon-writable and git
    honours it (aliases, core.fsmonitor) in a root process.
    """
    try:
        head = (Path(git_dir) / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not head.startswith("ref: "):
        return head if _SHA_RE.match(head) else ""
    ref = head[len("ref: "):].strip()
    loose = Path(git_dir) / ref
    try:
        candidate = loose.read_text(encoding="utf-8").strip()
    except OSError:
        candidate = ""
    if not _SHA_RE.match(candidate):
        candidate = ""
        try:
            packed = (Path(git_dir) / "packed-refs").read_text(encoding="utf-8")
        except OSError:
            packed = ""
        for line in packed.splitlines():
            if line.startswith("#") or line.startswith("^"):
                continue
            sha, _, name = line.partition(" ")
            if name.strip() == ref:
                candidate = sha.strip()
                break
    return candidate if _SHA_RE.match(candidate) else ""


def human_bytes(count: int) -> str:
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(value) < 1024 or unit == "TiB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def build_summary(categories: dict[str, int], errors: dict[str, str], before: int, after: int) -> str:
    parts = [f"{name} {human_bytes(size)}" for name, size in sorted(categories.items())]
    summary = (
        f"free {human_bytes(before)} -> {human_bytes(after)} "
        f"(reclaimed {human_bytes(sum(categories.values()))}); " + ", ".join(parts)
    )
    if errors:
        summary += "; failed: " + ", ".join(sorted(errors))
    return summary[:SUMMARY_CAP]


# ---------------------------------------------------------------------------
# Filesystem + docker actions
# ---------------------------------------------------------------------------


def _run(argv: list[str]) -> str:
    return subprocess.run(
        argv,
        capture_output=True,
        text=True,
        timeout=SUBPROCESS_TIMEOUT,
        check=True,
    ).stdout


def dir_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path, onerror=lambda _exc: None):
        dirs[:] = [d for d in dirs if not os.path.islink(os.path.join(root, d))]
        for name in files:
            try:
                total += os.lstat(os.path.join(root, name)).st_size
            except OSError:
                continue
    return total


def remove_tree(path: Path) -> int:
    """Delete a directory tree and return the bytes it held.

    Refuses protected paths and symlinks: root following a symlink the radon
    account planted is how an rm -rf becomes an incident.
    """
    if is_protected_path(path):
        raise ValueError(f"refusing to remove protected path: {path}")
    if has_symlink_component(path):
        raise ValueError(f"refusing to remove path reached through a symlink: {path}")
    if not path.is_dir():
        return 0
    size = dir_size(path)
    if DRY_RUN:
        print(f"[disk-cleanup] DRY RUN would remove {path} ({size} bytes)", file=sys.stderr)
        return size
    shutil.rmtree(path)
    return size


def cleanup_docker_images() -> tuple[int, str]:
    head = resolve_head_sha(LIVE_TREE / ".git")
    if not head:
        return 0, "HEAD unresolvable; refused to prune any image"
    images = parse_image_lines(
        _run([
            DOCKER_BIN, "image", "ls", "--no-trunc",
            "--format", "{{.Repository}}\t{{.Tag}}\t{{.ID}}\t{{.CreatedAt}}\t{{.Size}}",
        ])
    )
    prunable = select_prunable_images(images, head)
    _enforce_ceiling("docker-images", len(prunable), sum(i.size_bytes for i in prunable))
    reclaimed = 0
    removed = 0
    failed = 0
    for image in prunable:
        if DRY_RUN:
            reclaimed += image.size_bytes
            removed += 1
            continue
        try:
            # `_run` uses check=True, so the FIRST failing rm used to abort the
            # whole loop and discard the accounting: one image still referenced
            # by a stopped container meant zero reclaimed, week after week, on
            # a full root filesystem. R-371.
            _run([DOCKER_BIN, "image", "rm", f"{image.repository}:{image.tag}"])
        except Exception as exc:  # noqa: BLE001 - one image must not stop the sweep
            failed += 1
            print(
                f"[disk-cleanup] image rm {image.repository}:{image.tag} failed: {exc}",
                file=sys.stderr,
            )
            continue
        reclaimed += image.size_bytes
        removed += 1
    suffix = f", {failed} failed" if failed else ""
    return reclaimed, (
        f"removed {removed} image(s){suffix}, keeping HEAD {head[:12]} + "
        f"{KEEP_IMAGE_PAIRS} newest pairs + fallback {image_fallback_tag()}"
    )


def _enforce_ceiling(category: str, path_count: int, byte_count: int) -> None:
    """Refuse a category whose computed target set is implausibly large."""
    if path_count > MAX_PRUNE_PATHS:
        raise ReclaimCeilingExceeded(
            f"{category}: {path_count} targets exceeds MAX_PRUNE_PATHS "
            f"({MAX_PRUNE_PATHS}); refusing rather than deleting"
        )
    if byte_count > MAX_RECLAIM_BYTES:
        raise ReclaimCeilingExceeded(
            f"{category}: {byte_count} bytes exceeds MAX_RECLAIM_BYTES "
            f"({MAX_RECLAIM_BYTES}); refusing rather than deleting"
        )


def cleanup_dangling_images() -> tuple[int, str]:
    output = _run([DOCKER_BIN, "image", "prune", "-f"])
    return parse_reclaimed_space(output), "dangling/untagged layers pruned"


def cleanup_release_worktrees() -> tuple[int, str]:
    if not RELEASES_DIR.is_dir():
        return 0, "no releases dir"
    entries = []
    for child in RELEASES_DIR.iterdir():
        try:
            entries.append((child, child.lstat().st_mtime))
        except OSError:
            continue
    import time

    reclaimed = 0
    removed = 0
    prunable = select_prunable_releases(entries, time.time())
    _enforce_ceiling("release-worktrees", len(prunable), 0)
    for path in prunable:
        reclaimed += remove_tree(path)
        removed += 1

    admin_entries = []
    if WORKTREE_ADMIN_DIR.is_dir():
        for admin in WORKTREE_ADMIN_DIR.iterdir():
            if not admin.is_dir():
                continue
            try:
                gitdir = (admin / "gitdir").read_text(encoding="utf-8").strip()
            except OSError:
                gitdir = ""
            admin_entries.append((admin, bool(gitdir) and Path(gitdir).exists()))
    pruned_admin = 0
    for admin in select_stale_worktree_admin_dirs(admin_entries):
        # Through remove_tree, not a bare rmtree: the bare call bypassed
        # is_protected_path entirely and deleted inside /home/radon/radon/.git,
        # the tree this module's docstring asserts is unreachable. R-370.
        try:
            remove_tree(admin)
        except ValueError as exc:
            print(f"[disk-cleanup] refused admin dir {admin}: {exc}", file=sys.stderr)
            continue
        except OSError as exc:
            print(f"[disk-cleanup] admin dir {admin} vanished: {exc}", file=sys.stderr)
            continue
        pruned_admin += 1
    return (
        reclaimed,
        f"removed {removed} orphan(s) older than {RELEASE_MAX_AGE_DAYS}d, "
        f"pruned {pruned_admin} stale worktree record(s)",
    )


def cleanup_caches() -> tuple[int, str]:
    reclaimed = 0
    removed = []
    for target in CACHE_TARGETS:
        freed = remove_tree(target)
        if freed:
            reclaimed += freed
            removed.append(target.name)
    return reclaimed, f"cleared {', '.join(removed) or 'nothing'}"


def cleanup_journal() -> tuple[int, str]:
    journal_dir = Path("/var/log/journal")
    before = dir_size(journal_dir) if journal_dir.is_dir() else 0
    _run([JOURNALCTL_BIN, f"--vacuum-size={JOURNAL_VACUUM_SIZE}"])
    after = dir_size(journal_dir) if journal_dir.is_dir() else 0
    return max(before - after, 0), f"vacuumed to {JOURNAL_VACUUM_SIZE}"


CATEGORIES: tuple[tuple[str, Callable[[], tuple[int, str]]], ...] = (
    ("docker-images", cleanup_docker_images),
    ("docker-dangling", cleanup_dangling_images),
    ("release-worktrees", cleanup_release_worktrees),
    ("caches", cleanup_caches),
    ("journal", cleanup_journal),
)


def _free_space() -> int:
    return shutil.disk_usage("/").free


def run_cleanup(categories=None, free_space=None) -> dict:
    """Run every category, isolating failures, and return the heartbeat payload."""
    categories = CATEGORIES if categories is None else categories
    free_space = _free_space if free_space is None else free_space

    before = free_space()
    reclaimed: dict[str, int] = {}
    notes: dict[str, str] = {}
    errors: dict[str, str] = {}
    for name, action in categories:
        try:
            freed, note = action()
        except Exception as exc:  # noqa: BLE001 - one bad category must not stop the sweep
            errors[name] = f"{exc.__class__.__name__}: {exc}"[:200]
            reclaimed[name] = 0
            continue
        reclaimed[name] = freed
        notes[name] = note
    after = free_space()

    return {
        "state": "error" if errors else "ok",
        "detail": {
            "summary": build_summary(reclaimed, errors, before, after),
            "free_before_bytes": before,
            "free_after_bytes": after,
            "reclaimed_bytes": sum(reclaimed.values()),
            "categories": reclaimed,
            "notes": notes,
            "errors": errors,
        },
    }


# ---------------------------------------------------------------------------
# service_health heartbeat (stdlib libSQL HTTP pipeline -- bounded, no libsql)
# ---------------------------------------------------------------------------

_UPSERT_SQL = (
    "INSERT INTO service_health (service, state, last_attempt_started_at, "
    "last_attempt_finished_at, last_error, updated_at) VALUES (?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(service) DO UPDATE SET state = excluded.state, "
    "last_attempt_started_at = COALESCE(excluded.last_attempt_started_at, service_health.last_attempt_started_at), "
    "last_attempt_finished_at = COALESCE(excluded.last_attempt_finished_at, service_health.last_attempt_finished_at), "
    "last_error = excluded.last_error, "
    "updated_at = excluded.updated_at"
)

DB_CREDENTIAL_KEYS = ("TURSO_DB_URL", "TURSO_AUTH_TOKEN")


def http_url_from_libsql(url: str) -> str:
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://"):]
    return url


def load_env_keys(path: Path, keys: tuple[str, ...]) -> dict[str, str]:
    """Read an allowlisted set of keys out of an env file, as DATA.

    Same contract as drift_audit.load_env_keys: this process is root while the
    file is 0600 radon:radon, so nothing here touches os.environ and an
    appended LD_PRELOAD or PATH line is read past, not applied.
    """
    values: dict[str, str] = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition("=")
        if not sep or key.strip() not in keys:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key.strip()] = value
    return values


def resolve_db_credentials(environ) -> dict[str, str]:
    resolved = {
        key: environ.get(key, "") for key in DB_CREDENTIAL_KEYS if environ.get(key)
    }
    missing = [key for key in DB_CREDENTIAL_KEYS if key not in resolved]
    env_file = environ.get("RADON_ENV_FILE")
    if missing and env_file:
        resolved.update(load_env_keys(Path(env_file), tuple(missing)))
    return resolved


def _hrana_arg(value):
    return {"type": "null"} if value is None else {"type": "text", "value": value}


def write_service_health(state: str, detail: dict | None, started_at: str) -> None:
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
                            _hrana_arg(json.dumps(detail) if detail else None),
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


def main(argv: Optional[list[str]] = None) -> int:
    global DRY_RUN
    args = list(sys.argv[1:] if argv is None else argv)
    # --dry-run: run every selector and report every size, delete nothing.
    # Without it a selector regression reached production as an unrecoverable
    # rmtree on its first firing. R-368.
    if "--dry-run" in args:
        DRY_RUN = True
        print("[disk-cleanup] DRY RUN: nothing will be deleted", file=sys.stderr)

    started_at = datetime.now(timezone.utc).isoformat()
    with acquire_deploy_lock() as held:
        if not held:
            print(
                "[disk-cleanup] a deployment owns the production deploy lock; "
                "skipping this weekly reclaim",
                file=sys.stderr,
            )
            return EX_TEMPFAIL
        return _run_and_heartbeat(started_at)


def _run_and_heartbeat(started_at: str) -> int:
    try:
        outcome = run_cleanup()
    except Exception as exc:  # noqa: BLE001 - a crash must still heartbeat
        crash = {"summary": f"disk cleanup crashed: {exc.__class__.__name__}: {exc}"[:SUMMARY_CAP]}
        print(crash["summary"], file=sys.stderr)
        try:
            write_service_health("error", crash, started_at)
        except Exception as write_exc:  # noqa: BLE001
            print(f"service_health write failed: {write_exc}", file=sys.stderr)
        return 1

    detail = outcome["detail"]
    print(f"disk-cleanup: {detail['summary']}")
    for name, note in sorted(detail["notes"].items()):
        print(f"  {name}: {human_bytes(detail['categories'][name])} -- {note}")
    for name, message in sorted(detail["errors"].items()):
        print(f"  FAILED {name}: {message}", file=sys.stderr)

    try:
        write_service_health(outcome["state"], detail, started_at)
    except Exception as exc:  # noqa: BLE001 - bounded write, surface the failure
        print(f"service_health write failed: {exc}", file=sys.stderr)
        return 1
    print(f"service_health row written: {SERVICE_NAME} = {outcome['state']}")
    # A failing category is reported through the heartbeat, not by failing the
    # unit: a wedged docker daemon must not also park the timer (DUR-02).
    return 0


if __name__ == "__main__":
    sys.exit(main())
