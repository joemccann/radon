"""Resolve the radon clone root and reject laptop-home pins in nightly surfaces.

Used by the native security-audit workflows (via the JS twin) and by the CI
guard that fails if a tracked workflow or loop wrapper hardcodes ``/Users/``.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

RADON_REPO_ROOT_ENV = "RADON_REPO_ROOT"
RADON_WEEKEND_REPO_ENV = "RADON_WEEKEND_REPO"

# A real home directory, not the sanitizer ellipsis "/Users/...".
HARDCODED_USER_HOME_RE = re.compile(r"/Users/[A-Za-z][A-Za-z0-9._-]*")

WORKFLOW_PREFIX = ".claude/workflows/"
SCRIPT_TEST_PREFIXES = ("scripts/tests/", "scripts/api/tests/")


class NotARadonCheckout(ValueError):
    """The resolved directory is missing radon markers."""


@dataclass(frozen=True)
class UserHomeHit:
    path: Path
    line: int
    text: str


def is_radon_checkout(path: Path | str) -> bool:
    root = Path(path)
    claude = root / "CLAUDE.md"
    if not claude.is_file():
        return False
    try:
        text = claude.read_text(encoding="utf-8")
    except OSError:
        return False
    if "RADON" not in text.upper():
        return False
    if not (root / "scripts" / "evaluate.py").is_file():
        return False
    return (root / ".claude" / "workflows" / "security-audit.mjs").is_file()


def git_toplevel(cwd: Path | str) -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(cwd),
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.strip()


def resolve_radon_repo_root(
    *,
    env: dict[str, str] | None = None,
    cwd: Path | str | None = None,
    git_toplevel=None,
) -> Path:
    """``RADON_REPO_ROOT``, then ``RADON_WEEKEND_REPO``, then git, then cwd."""
    environ = env if env is not None else os.environ
    working = Path(cwd if cwd is not None else Path.cwd())
    git_fn = git_toplevel if git_toplevel is not None else git_toplevel_from_cwd

    explicit = str(environ.get(RADON_REPO_ROOT_ENV, "") or "").strip()
    weekend = str(environ.get(RADON_WEEKEND_REPO_ENV, "") or "").strip()

    if explicit:
        return _require_radon(explicit, RADON_REPO_ROOT_ENV)
    if weekend:
        return _require_radon(weekend, RADON_WEEKEND_REPO_ENV)

    git_root = str(git_fn(working) or "").strip()
    if git_root:
        candidate = Path(git_root)
        if is_radon_checkout(candidate):
            return candidate.resolve()
    if is_radon_checkout(working):
        return working.resolve()
    raise NotARadonCheckout(
        f"resolved directory is not a radon checkout (cwd={working}). "
        f"Set {RADON_REPO_ROOT_ENV} to the clone root."
    )


def git_toplevel_from_cwd(cwd: Path | str) -> str:
    return git_toplevel(cwd)


def _require_radon(path: str, source: str) -> Path:
    candidate = Path(path)
    if is_radon_checkout(candidate):
        return candidate.resolve()
    raise NotARadonCheckout(
        f"{source}={candidate} is not a radon checkout. "
        f"Set {RADON_REPO_ROOT_ENV} to the clone root."
    )


def is_guarded_relpath(rel: str) -> bool:
    posix = rel.replace("\\", "/")
    if posix.startswith(WORKFLOW_PREFIX):
        return True
    if posix.startswith(SCRIPT_TEST_PREFIXES):
        return False
    if posix.startswith("scripts/") and "/tests/" in posix:
        return False
    if posix.startswith("scripts/") and posix.endswith(".sh") and posix.count("/") == 1:
        return True
    if posix.startswith("scripts/") and posix.endswith(".py"):
        name = Path(posix).name
        return "nightly" in name or "weekend" in name
    return False


def tracked_guard_files(repo: Path) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    rels = [part.decode("utf-8") for part in proc.stdout.split(b"\0") if part]
    return [repo / rel for rel in rels if is_guarded_relpath(rel)]


def find_hardcoded_user_homes(
    repo: Path,
    *,
    files: Iterable[Path] | None = None,
    tracked_only: bool = True,
) -> list[UserHomeHit]:
    targets = list(files) if files is not None else tracked_guard_files(repo)
    hits: list[UserHomeHit] = []
    for path in targets:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            if HARDCODED_USER_HOME_RE.search(line):
                hits.append(UserHomeHit(path=path, line=index, text=line.strip()))
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args(argv)
    if not args.check:
        parser.print_help()
        return 2
    hits = find_hardcoded_user_homes(Path(args.repo))
    if not hits:
        return 0
    for hit in hits:
        print(f"{hit.path}:{hit.line}:{hit.text}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
