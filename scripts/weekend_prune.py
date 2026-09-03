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
  * any ``web/node_modules`` (a deleted one breaks the next remediate phase)
  * any ``venv-*`` / ``.venv`` (the per-loop interpreters)
  * any ``.deepsec`` export and any private ``*scratch*`` dir (audit state)
  * a loop clone directory itself
  * anything outside the weekend root and the OS temp dir
  * a git worktree whose branch has commits not on its remote, or dirty
  * every path under a clone whose ``.weekend-runner.lock`` is held by a live
    pid — a cycle is running in it right now

Stdlib only, like ``weekend_notify.py``: the wrappers call it with whatever
``python3`` is on PATH, at the end of a cycle, best-effort. It never touches
anything outside the root it is given and it never fails the run that called
it — the wrappers discard its exit code.

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
GIT_TIMEOUT_SECS = 30
RUNNER_LOCK = ".weekend-runner.lock"
RUNNER_MARKER = ".radon-weekend-runner"
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
    if name == "node_modules":
        return "protected: node_modules (a remediate phase needs it to run vitest)"
    if name == "venv" or name == ".venv" or name.startswith("venv-"):
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


def _locked_clones(root: Path) -> Dict[str, int]:
    """realpath -> live pid, for every clone under ``root`` with a held lock."""
    held: Dict[str, int] = {}
    try:
        children = sorted(root.iterdir())
    except OSError:
        return held
    for child in children:
        if not child.is_dir() or child.is_symlink() or not _is_loop_clone(child):
            continue
        pid = locking_pid(child)
        if pid is not None:
            held[os.path.realpath(child)] = pid
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

    if base == root_real:
        if len(parts) == 1 and _is_loop_clone(Path(real)):
            return "protected: loop clone directory"
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
        if not logs.is_dir():
            continue
        for label_dir in sorted(logs.iterdir()):
            if not label_dir.is_dir() or label_dir.is_symlink():
                continue
            for entry in sorted(label_dir.iterdir()):
                if not entry.is_file() or entry.is_symlink():
                    continue
                if entry.name.startswith(LAUNCHD_SINKS):
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


def _worktree_verdict(clone: Path, wt: Path, branch: str) -> Optional[str]:
    """Why this worktree must be kept, or None when it is safe to remove."""
    if not branch:
        return "refused: worktree is detached (no branch to compare)"
    status = _git(wt, "status", "--porcelain")
    if status is None:
        return "refused: worktree status unavailable"
    if status.strip():
        return "refused: dirty worktree (uncommitted work)"
    ahead = _git(wt, "rev-list", "--count", "@{upstream}..HEAD")
    if ahead is None:
        return f"refused: unpushed branch {branch} (no upstream on the remote)"
    try:
        count = int(ahead.strip())
    except ValueError:
        return "refused: worktree ahead-count unavailable"
    if count > 0:
        return f"refused: {count} unpushed commit(s) on {branch}"
    return None


def _worktrees(root: Path, locked: Dict[str, int]) -> Tuple[List[Tuple[Path, Path]], List[Dict[str, str]]]:
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
                        reason = _worktree_verdict(clone, wt_path, branch)
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


def run(*, root, temp_root=None, dry_run: bool = False, now: Optional[float] = None) -> dict:
    root = Path(root)
    temp_root = Path(temp_root) if temp_root is not None else Path(tempfile.gettempdir())
    now = time.time() if now is None else now

    locked = _locked_clones(root)
    refused: List[Dict[str, str]] = [
        {"path": path, "reason": f"refused: runner lock held by live pid {pid}"}
        for path, pid in sorted(locked.items())
    ]

    removable_worktrees, worktree_refusals = _worktrees(root, locked)
    refused.extend(worktree_refusals)

    plan: Dict[str, List[Path]] = {
        "pycache": _walk_candidates(root, locked, "__pycache__"),
        "pytest_cache": _walk_candidates(root, locked, ".pytest_cache"),
        "run_logs": _stale_run_logs(root, locked, now),
        "worktrees": [wt for _clone, wt in removable_worktrees],
        "tmp_pytest": _tmp_pytest(temp_root, now),
    }
    worktree_clone = {os.path.realpath(wt): clone for clone, wt in removable_worktrees}

    free_before = shutil.disk_usage(str(root)).free
    categories: Dict[str, dict] = {}
    for name in CATEGORIES:
        done: List[str] = []
        total = 0
        for candidate in plan.get(name, []):
            real = os.path.realpath(candidate)
            try:
                if name == "worktrees":
                    size = _du(Path(candidate))
                    reason = refusal_reason(candidate, root=root, temp_root=root, locked=locked)
                    if reason is not None:
                        raise Refused(f"{candidate}: {reason}")
                    if not dry_run:
                        # A registry op on the OWNING clone, never an rm -rf:
                        # git refuses when the worktree is busy or dirty.
                        if _git(worktree_clone[real], "worktree", "remove", str(candidate)) is None:
                            refused.append({"path": real,
                                            "reason": "refused: git worktree remove declined"})
                            continue
                else:
                    size = remove_candidate(candidate, root=root, temp_root=temp_root,
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
        lines.append(f"[prune] kept {entry['path']} — {entry['reason']}")
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
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    root = Path(args.root)
    problem = validate_root(root)
    if problem:
        print(f"[prune] {problem}", file=sys.stderr)
        return 2

    report = run(root=root, temp_root=Path(args.temp_dir), dry_run=args.dry_run)
    if args.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
