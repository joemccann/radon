#!/usr/bin/env python3
"""Durable per-runner prune of the nightly-loop root (``~/radon-weekend``).

Five loop clones, five per-loop venvs, a private audit scratch dir and every
worktree a remediate phase ever left behind share one directory that nothing
reclaimed between fires. It crept up until a human had to go in with ``rm
-rf`` — by hand, in the one directory that also holds the only copy of
unpushed work and the bootstrapped ``web/node_modules`` a remediate phase
needs to run vitest at all.

This is the opposite design. It is an **allowlist**: ``CATEGORIES`` is the
complete set of things it may delete, and ``refusal_reason`` is consulted for
every single candidate immediately before the unlink, so a category that ever
enumerates something protected still cannot delete it. Everything not named
in a category is left alone — including anything it has never heard of.

Refused by construction, never by pattern-matching what to avoid:
  * any ``web/node_modules`` under a loop CLONE (a deleted one breaks the
    next remediate phase's vitest run)
  * any ``venv-*`` / ``.venv`` under a loop CLONE (the per-loop interpreters)
  * any ``.deepsec`` export and any private ``*scratch*`` dir (audit state)
  * a loop clone directory itself
  * anything outside the weekend root; only ``tmp_pytest`` may look at the OS
    temp dir at all, and only at ``pytest-of-*`` trees inside it
  * a git worktree that is dirty, has commits reachable from no remote,
    carries a ``.weekend-keep`` marker, whose newest commit is younger than
    ``WORKTREE_MIN_IDLE_DAYS``, or which HOLDS an env file or audit state —
    ``git worktree remove`` deletes gitignored content, so that last check
    reads the worktree's CONTENTS, not only its path. A ``node_modules`` or
    a ``.venv`` inside an already-idle, already-pushed worktree is not a
    refusal: it belongs to nothing that will run, and it is the bulk of what
    this step exists to reclaim.
  * every path under a clone whose ``.weekend-runner.lock`` is held by a live
    pid — a cycle is running in it right now

Stdlib only and 3.9-clean, like ``weekend_notify.py``. The wrappers pipe the
``origin/main`` copy into ``/usr/bin/python3 -I -`` rather than exec a python
FILE out of the agent-writable clone; that stops a planted working-tree file
and a planted ``json.py`` on ``sys.path``, and nothing more (the ref it reads
lives in the same clone). It never touches anything outside the root it is
given and it never fails the run that called it — the wrappers discard its
exit code.

    python3 scripts/weekend_prune.py --root ~/radon-weekend [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# The complete allowlist. Nothing outside these categories is ever a
# candidate; adding one is a code change with a test.
CATEGORIES: Tuple[str, ...] = (
    "pycache",       # __pycache__ dirs outside every protected tree
    "pytest_cache",  # .pytest_cache dirs outside every protected tree
    "run_logs",      # per-phase loop logs older than RUN_LOG_MAX_AGE_DAYS
    "worktrees",     # git worktrees fully pushed AND clean
    "tmp_pytest",    # abandoned pytest tmp trees in the OS temp dir
)

RUN_LOG_MAX_AGE_DAYS = 30
TMP_MAX_AGE_DAYS = 2
# A worktree is only ever a candidate once its newest commit has gone quiet.
# Clean + pushed is not idle: a branch awaiting CI triage looks exactly like an
# abandoned one, and re-adding it costs a human a checkout.
WORKTREE_MIN_IDLE_DAYS = 3
GIT_TIMEOUT_SECS = 30
RUNNER_LOCK = ".weekend-runner.lock"
RUNNER_MARKER = ".radon-weekend-runner"
# Drop this file in a worktree to keep it forever, whatever its age.
WORKTREE_KEEP_MARKER = ".weekend-keep"
# `git worktree remove` deletes gitignored files, so a worktree holding one of
# these is refused whole. Env files carry hand-provisioned credentials and are
# not rebuildable by a command; audit state is private and not reproducible.
PROTECTED_WORKTREE_FILES = (".env", ".env.local", ".env.ib-mode")
# Never rotated away: the plists point StandardOutPath/StandardErrorPath here
# and only bump mtime when something writes, so they sort old (R-267).
LAUNCHD_SINKS = ("launchd-",)


class Refused(Exception):
    """A candidate hit the allowlist's refusal check at deletion time."""


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------
def _protected_part(name: str) -> Optional[str]:
    """Why a single path component makes its whole subtree untouchable."""
    lowered = name.lower()
    # macOS is case-insensitive: NODE_MODULES/ resolves to the same tree, so
    # the refusal has to be case-insensitive too.
    if lowered == "node_modules":
        return "protected: node_modules (a remediate phase needs it to run vitest)"
    if lowered in ("venv", ".venv") or lowered.startswith("venv-"):
        return "protected: venv (a loop's python interpreter)"
    if lowered in (".deepsec", "deepsec"):
        return "protected: deepsec audit state"
    if "scratch" in lowered:
        return "protected: private scratch state"
    return None


def _is_loop_clone(path: Path) -> bool:
    """A loop clone: the runner marker, or a real (non-worktree) .git dir."""
    return (path / RUNNER_MARKER).exists() or (path / ".git").is_dir()


def _relative_parts(real: str, base: str) -> Optional[List[str]]:
    if real == base:
        return []
    prefix = base.rstrip(os.sep) + os.sep
    if not real.startswith(prefix):
        return None
    return real[len(prefix):].split(os.sep)


def locking_pid(clone: Path) -> Optional[int]:
    """The live pid holding ``clone``'s runner lock, if any.

    The lock is a directory with a ``pid`` file (mkdir is the atomic primitive
    on macOS; flock(1) does not exist there). A plain file holding the pid is
    accepted too. A pid we cannot signal because it belongs to another user is
    ALIVE — absent evidence is not evidence of staleness (R-411).
    """
    lock = clone / RUNNER_LOCK
    raw = ""
    try:
        if lock.is_dir():
            raw = (lock / "pid").read_text(encoding="utf-8", errors="replace")
        elif lock.is_file():
            raw = lock.read_text(encoding="utf-8", errors="replace")
        else:
            return None
    except OSError:
        # A lock we cannot read is a lock we must assume is held.
        return -1
    raw = raw.strip()
    if not raw:
        # Held, between the winner's mkdir and its pid write (R-411).
        return -1
    try:
        pid = int(raw)
    except ValueError:
        return -1
    if pid <= 0:
        return -1
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return pid
    except OSError:
        return pid
    return pid


def _locked_clones(root: Path, caller: Optional[str] = None) -> Dict[str, int]:
    """realpath -> live pid, for every clone under ``root`` with a held lock.

    ``caller`` is the clone whose wrapper is running this prune. It holds its
    own lock for the whole cycle and calls us from inside it, so honouring
    that one lock would mean the loop that generates the garbage is the only
    clone that can never clean it. Every other refusal still applies to it.
    """
    held: Dict[str, int] = {}
    caller_real = os.path.realpath(caller) if caller else None
    try:
        children = sorted(root.iterdir())
    except OSError:
        return held
    for child in children:
        if not child.is_dir() or child.is_symlink() or not _is_loop_clone(child):
            continue
        real = os.path.realpath(child)
        if caller_real is not None and real == caller_real:
            continue
        pid = locking_pid(child)
        if pid is not None:
            held[real] = pid
    return held


def refusal_reason(
    path,
    *,
    root,
    temp_root,
    locked: Optional[Dict[str, int]] = None,
) -> Optional[str]:
    """Why ``path`` may NOT be deleted, or None if it is inside the allowlist.

    Consulted for every candidate immediately before the unlink, not only when
    the candidate is enumerated.
    """
    p = Path(path)
    real = os.path.realpath(str(p))
    root_real = os.path.realpath(str(root))
    temp_real = os.path.realpath(str(temp_root))

    if real == os.sep:
        return "refused: outside the weekend root (filesystem root)"
    parts = _relative_parts(real, root_real)
    base = root_real
    if parts is None:
        parts = _relative_parts(real, temp_real)
        base = temp_real
        if parts is None:
            return "refused: outside the weekend root and the OS temp dir"
    if not parts:
        return "refused: the weekend root itself is never a candidate"
    # Containment is judged on the RESOLVED path first, so a symlink pointing
    # out of the root is reported as what it is; the link itself is still
    # never followed or deleted.
    if p.is_symlink():
        return "refused: symlink (never followed)"

    for part in parts:
        reason = _protected_part(part)
        if reason is not None:
            return reason

    if base == root_real and len(parts) == 1 and _is_loop_clone(Path(real)):
        return "protected: loop clone directory"
    # Not gated on `base`: a clone registered under the OS temp dir gets the
    # same lock refusal a clone under the root does.
    for held_real, pid in (locked or {}).items():
        if real == held_real or real.startswith(held_real.rstrip(os.sep) + os.sep):
            return f"refused: runner lock held by live pid {pid}"
    return None


# --------------------------------------------------------------------------
# Sizing and deletion
# --------------------------------------------------------------------------
def _du(path: Path) -> int:
    if path.is_symlink():
        return 0
    try:
        if path.is_file():
            return path.lstat().st_size
    except OSError:
        return 0
    total = 0
    for dirpath, _dirnames, filenames in os.walk(str(path), onerror=lambda _e: None):
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                continue
    return total


def remove_candidate(path, *, root, temp_root, locked=None, dry_run: bool = False) -> int:
    """Delete one candidate after re-checking the allowlist. Returns bytes."""
    reason = refusal_reason(path, root=root, temp_root=temp_root, locked=locked)
    if reason is not None:
        raise Refused(f"{path}: {reason}")
    p = Path(path)
    size = _du(p)
    if dry_run:
        return size
    if p.is_dir():
        shutil.rmtree(str(p), ignore_errors=True)
    else:
        try:
            p.unlink()
        except OSError:
            return 0
    return size


# --------------------------------------------------------------------------
# Enumeration — one function per allowlist category
# --------------------------------------------------------------------------
def _walk_candidates(root: Path, locked: Dict[str, int], wanted: str) -> List[Path]:
    """Directories named ``wanted``, never descending a protected subtree."""
    found: List[Path] = []
    for dirpath, dirnames, _filenames in os.walk(str(root), onerror=lambda _e: None):
        keep = []
        for name in dirnames:
            child = os.path.join(dirpath, name)
            if os.path.islink(child):
                continue
            if name == ".git" or _protected_part(name) is not None:
                continue
            real_child = os.path.realpath(child)
            if any(real_child == h or real_child.startswith(h.rstrip(os.sep) + os.sep)
                   for h in locked):
                continue
            if name == wanted:
                found.append(Path(child))
                continue  # do not descend into what we are about to delete
            keep.append(name)
        dirnames[:] = keep
    return found


def _stale_run_logs(root: Path, locked: Dict[str, int], now: float) -> List[Path]:
    cutoff = now - RUN_LOG_MAX_AGE_DAYS * 86400
    found: List[Path] = []
    try:
        clones = sorted(root.iterdir())
    except OSError:
        return found
    for clone in clones:
        if not clone.is_dir() or clone.is_symlink() or not _is_loop_clone(clone):
            continue
        if os.path.realpath(clone) in locked:
            continue
        logs = clone / "logs"
        # `logs` itself may be a symlink; is_dir() follows it, and rotation
        # would then delete whatever it points at (another clone's .git, say).
        if logs.is_symlink() or not logs.is_dir():
            continue
        confine = os.path.realpath(clone) + os.sep
        for label_dir in sorted(logs.iterdir()):
            if not label_dir.is_dir() or label_dir.is_symlink():
                continue
            if label_dir.name == ".git":
                continue
            for entry in sorted(label_dir.iterdir()):
                if not entry.is_file() or entry.is_symlink():
                    continue
                if entry.name.startswith(LAUNCHD_SINKS):
                    continue
                # Every candidate must still RESOLVE inside its own clone.
                if not os.path.realpath(entry).startswith(confine):
                    continue
                try:
                    if entry.stat().st_mtime < cutoff:
                        found.append(entry)
                except OSError:
                    continue
    return found


def _git(clone: Path, *args: str) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(clone), *args],
            capture_output=True, text=True, timeout=GIT_TIMEOUT_SECS, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _protected_content(wt: Path) -> Optional[str]:
    """Why the CONTENTS of ``wt`` make it untouchable.

    ``git worktree remove`` deletes gitignored files, so ``status
    --porcelain`` — which never lists an ignored file — is not the whole
    check, and applying ``refusal_reason`` to the worktree PATH says nothing
    about what is inside it.

    What counts as untouchable here is narrower than ``_protected_part``, on
    purpose. That function protects a live loop CLONE, where ``web/
    node_modules`` is the bootstrap a remediate phase needs to run vitest at
    all and ``venv-*`` is a loop's interpreter — deleting either breaks the
    next fire. A worktree only reaches this function after it has been proven
    clean, fully reachable from a remote, past the idle floor and unmarked,
    so a ``node_modules`` or ``.venv`` inside it belongs to nothing that is
    going to run, and both are one command to rebuild: they are precisely the
    garbage this step exists to reclaim. Env files and audit state are not
    rebuildable by a command, so those still refuse the whole worktree.
    """
    for dirpath, dirnames, filenames in os.walk(str(wt), onerror=lambda _e: None):
        keep = []
        for name in dirnames:
            lowered = name.lower()
            if lowered in (".deepsec", "deepsec") or "scratch" in lowered:
                return f"{name}/ (audit state, not reproducible)"
            if name == ".git" or os.path.islink(os.path.join(dirpath, name)):
                continue
            # Not descended: nothing inside a node_modules or a venv can
            # refuse the worktree, and walking one costs a hundred thousand
            # stat calls.
            if _protected_part(name) is None:
                keep.append(name)
        dirnames[:] = keep
        for name in filenames:
            if name in PROTECTED_WORKTREE_FILES:
                return f"{name} (a provisioned env file)"
    return None


def _worktree_verdict(clone: Path, wt: Path, branch: str, now: float) -> Optional[str]:
    """Why this worktree must be kept, or None when it is safe to remove."""
    if not branch:
        return "refused: worktree is detached (no branch to compare)"
    if (wt / WORKTREE_KEEP_MARKER).exists():
        return f"refused: {WORKTREE_KEEP_MARKER} marker"
    status = _git(wt, "status", "--porcelain")
    if status is None:
        return "refused: worktree status unavailable"
    if status.strip():
        return "refused: dirty worktree (uncommitted work)"
    # @{upstream} may be a LOCAL branch (branch.<b>.remote = .). Commits that
    # exist on no remote at all would then read as 0 ahead.
    upstream = _git(wt, "rev-parse", "--symbolic-full-name", "@{upstream}")
    if upstream is None or not upstream.strip().startswith("refs/remotes/"):
        return f"refused: unpushed branch {branch} (no remote-tracking upstream)"
    ahead = _git(wt, "rev-list", "--count", "@{upstream}..HEAD")
    if ahead is None:
        return f"refused: unpushed branch {branch} (no upstream on the remote)"
    try:
        count = int(ahead.strip())
    except ValueError:
        return "refused: worktree ahead-count unavailable"
    if count > 0:
        return f"refused: {count} unpushed commit(s) on {branch}"
    off_remote = _git(wt, "rev-list", "--count", "HEAD", "--not", "--remotes")
    if off_remote is None:
        return "refused: worktree remote-reachability unavailable"
    try:
        off = int(off_remote.strip())
    except ValueError:
        return "refused: worktree remote-reachability unavailable"
    if off > 0:
        return f"refused: {off} commit(s) on {branch} reachable from no remote"
    # Clean and pushed is not idle. A branch awaiting CI triage is
    # indistinguishable from an abandoned one except by age.
    committed = _git(wt, "log", "-1", "--format=%ct", "HEAD")
    if committed is None:
        return "refused: worktree commit date unavailable"
    try:
        stamp = float(committed.strip())
    except ValueError:
        return "refused: worktree commit date unavailable"
    idle_days = (now - stamp) / 86400.0
    if idle_days < WORKTREE_MIN_IDLE_DAYS:
        return (f"refused: {branch} last committed {idle_days:.1f}d ago "
                f"(idle floor {WORKTREE_MIN_IDLE_DAYS}d)")
    held = _protected_content(wt)
    if held is not None:
        return f"refused: worktree holds {held}"
    return None


def _worktrees(root: Path, locked: Dict[str, int], now: float) -> Tuple[List[Tuple[Path, Path]], List[Dict[str, str]]]:
    removable: List[Tuple[Path, Path]] = []
    refused: List[Dict[str, str]] = []
    try:
        clones = sorted(root.iterdir())
    except OSError:
        return removable, refused
    for clone in clones:
        if not clone.is_dir() or clone.is_symlink() or not _is_loop_clone(clone):
            continue
        if os.path.realpath(clone) in locked:
            continue
        listing = _git(clone, "worktree", "list", "--porcelain")
        if listing is None:
            continue
        wt_path: Optional[Path] = None
        branch = ""
        for line in listing.splitlines() + [""]:
            if line.startswith("worktree "):
                wt_path = Path(line[len("worktree "):])
                branch = ""
            elif line.startswith("branch "):
                branch = line[len("branch "):].split("/")[-1]
            elif not line.strip() and wt_path is not None:
                if os.path.realpath(wt_path) != os.path.realpath(clone):
                    reason = refusal_reason(wt_path, root=root, temp_root=root, locked=locked)
                    if reason is None:
                        reason = _worktree_verdict(clone, wt_path, branch, now)
                    if reason is None:
                        removable.append((clone, wt_path))
                    else:
                        refused.append({"path": os.path.realpath(wt_path), "reason": reason})
                wt_path = None
                branch = ""
    return removable, refused


def _tmp_pytest(temp_root: Path, now: float) -> List[Path]:
    cutoff = now - TMP_MAX_AGE_DAYS * 86400
    found: List[Path] = []
    if not temp_root.is_dir():
        return found
    try:
        owners = sorted(temp_root.glob("pytest-of-*"))
    except OSError:
        return found
    for owner in owners:
        if not owner.is_dir() or owner.is_symlink():
            continue
        for run in sorted(owner.glob("pytest-*")):
            if not run.is_dir() or run.is_symlink():
                continue
            try:
                if run.stat().st_mtime < cutoff:
                    found.append(run)
            except OSError:
                continue
    return found


# --------------------------------------------------------------------------
# The run
# --------------------------------------------------------------------------
def _human(num: float) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(num) < 1024 or unit == "TiB":
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024.0
    return f"{num:.1f} TiB"


def run(*, root, temp_root=None, dry_run: bool = False, now: Optional[float] = None,
        caller: Optional[str] = None) -> dict:
    root = Path(root)
    temp_root = Path(temp_root) if temp_root is not None else Path(tempfile.gettempdir())
    now = time.time() if now is None else now

    locked = _locked_clones(root, caller)
    refused: List[Dict[str, str]] = [
        {"path": path, "reason": f"refused: runner lock held by live pid {pid}"}
        for path, pid in sorted(locked.items())
    ]

    removable_worktrees, worktree_refusals = _worktrees(root, locked, now)
    refused.extend(worktree_refusals)

    plan: Dict[str, List[Path]] = {
        "pycache": _walk_candidates(root, locked, "__pycache__"),
        "pytest_cache": _walk_candidates(root, locked, ".pytest_cache"),
        "run_logs": _stale_run_logs(root, locked, now),
        "worktrees": [wt for _clone, wt in removable_worktrees],
        "tmp_pytest": _tmp_pytest(temp_root, now),
    }
    worktree_clone = {os.path.realpath(wt): clone for clone, wt in removable_worktrees}

    # Enumeration is the slow part. Re-read the locks immediately before the
    # first unlink and honour the UNION, so a cycle that acquired its lock
    # while we were walking is still refused (a released one stays refused
    # too — that only costs us one night's reclaim).
    locked = dict(locked)
    locked.update(_locked_clones(root, caller))

    free_before = shutil.disk_usage(str(root)).free
    categories: Dict[str, dict] = {}
    for name in CATEGORIES:
        done: List[str] = []
        total = 0
        # Only tmp_pytest may reach outside the root at all; every other
        # category is confined to the root even when $TMPDIR points elsewhere.
        cat_temp = temp_root if name == "tmp_pytest" else root
        for candidate in plan.get(name, []):
            real = os.path.realpath(candidate)
            try:
                if name == "worktrees":
                    # Re-checked immediately before the removal, not only at
                    # enumeration: the path refusal, AND the contents, because
                    # `git worktree remove` takes gitignored files with it.
                    reason = refusal_reason(candidate, root=root, temp_root=root, locked=locked)
                    if reason is None:
                        held = _protected_content(Path(candidate))
                        if held is not None:
                            reason = f"refused: worktree holds {held}"
                    if reason is not None:
                        raise Refused(f"{candidate}: {reason}")
                    size = _du(Path(candidate))
                    if not dry_run:
                        # A registry op on the OWNING clone, never an rm -rf:
                        # git refuses when the worktree is busy or dirty.
                        if _git(worktree_clone[real], "worktree", "remove", str(candidate)) is None:
                            refused.append({"path": real,
                                            "reason": "refused: git worktree remove declined"})
                            continue
                else:
                    size = remove_candidate(candidate, root=root, temp_root=cat_temp,
                                            locked=locked, dry_run=dry_run)
            except Refused as exc:
                refused.append({"path": real, "reason": str(exc)})
                continue
            total += size
            done.append(real)
        categories[name] = {"bytes": total, "items": len(done), "paths": done}

    free_after = shutil.disk_usage(str(root)).free
    return {
        "root": os.path.realpath(root),
        "temp_dir": os.path.realpath(temp_root),
        "dry_run": bool(dry_run),
        "categories": categories,
        "refused": refused,
        "reclaimed_bytes": sum(c["bytes"] for c in categories.values()),
        "free_before_bytes": free_before,
        "free_after_bytes": free_after,
    }


def format_report(report: dict) -> str:
    """The shape the loops already use: one `[prune] ...` line per fact."""
    lines = [
        "[prune] root={root} free_before={free}{dry}".format(
            root=report["root"],
            free=_human(report["free_before_bytes"]),
            dry=" (dry-run)" if report["dry_run"] else "",
        )
    ]
    for name in CATEGORIES:
        cat = report["categories"][name]
        lines.append(
            f"[prune] {name:<13} {_human(cat['bytes']):>10}  {cat['items']} item(s)"
        )
    for entry in report["refused"]:
        lines.append(f"[prune] kept {entry['path']} - {entry['reason']}")
    lines.append(
        "[prune] reclaimed {rec} free_after={after}".format(
            rec=_human(report["reclaimed_bytes"]),
            after=_human(report["free_after_bytes"]),
        )
    )
    return "\n".join(lines)


def validate_root(root: Path) -> Optional[str]:
    """Refuse to run against anything that is not a nightly-loop root."""
    if not root.is_dir():
        return f"refused: {root} is not a directory"
    real = Path(os.path.realpath(root))
    if str(real) == os.sep or real.parent == real:
        return "refused: the filesystem root is not a weekend root"
    if str(real) == os.path.realpath(os.path.expanduser("~")):
        return "refused: the home directory is not a weekend root"
    if real.name == "radon-weekend":
        return None
    try:
        for child in real.iterdir():
            if child.is_dir() and (child / RUNNER_MARKER).exists():
                return None
    except OSError:
        pass
    return (f"refused: {real} carries no loop clone ({RUNNER_MARKER}) "
            "and is not named radon-weekend")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=os.path.expanduser("~/radon-weekend"))
    parser.add_argument("--temp-dir", default=tempfile.gettempdir())
    parser.add_argument("--self", dest="caller", default=None,
                        help="the calling clone: ignore ITS runner lock, so the "
                             "loop that made the garbage can clean it")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    root = Path(args.root)
    problem = validate_root(root)
    if problem:
        print(f"[prune] {problem}", file=sys.stderr)
        return 2

    report = run(root=root, temp_root=Path(args.temp_dir), dry_run=args.dry_run,
                 caller=args.caller)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
