#!/usr/bin/env python3
"""Semver for the Radon app.

0.7.0 is pinned at eb5e5206. Each later non-merge commit that touches the
running app moves one digit: a breaking marker moves major, ``feat`` moves
minor, and any other runtime change moves patch. Docs, tests, CI, lockfiles,
and the version fields themselves do not move it.

PRs never edit the version. The node image build passes this fold in as
``RADON_RELEASE_VERSION`` and CI tags ``vX.Y.Z`` after a green deploy. The
``package.json`` fields are only a fallback when no history is available, and
``--check`` is an optional local audit, not a CI gate. A stale tab notices the
SHA, including when the digits did not move.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_SHA = "eb5e52066462374b3426c0ec874c6c9024bb5b8b"
BASE_VERSION = (0, 7, 0)
ROOT = Path(__file__).resolve().parents[1]

CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(?:\([^)]+\))?(?P<breaking>!)?:")
VERSION_KEY = re.compile(r'("version"\s*:\s*")(\d+\.\d+\.\d+)(")')
SKIP_PATHS = {
    "package.json",
    "web/package.json",
    "bun.lock",
    "web/bun.lock",
    "package-lock.json",
    "web/package-lock.json",
}
RUNTIME_ROOTS = ("web/", "scripts/", "cloud/")


def base_sha() -> str:
    return os.environ.get("RADON_RELEASE_BASE_SHA", BASE_SHA)


def base_version() -> tuple[int, int, int]:
    raw = os.environ.get("RADON_RELEASE_BASE_VERSION")
    return parse_version(raw) if raw else BASE_VERSION


def parse_version(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    if not match:
        raise ValueError(f"not a semver: {text}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def format_version(version: tuple[int, int, int]) -> str:
    return f"{version[0]}.{version[1]}.{version[2]}"


def is_runtime(path: str) -> bool:
    if path in SKIP_PATHS or path.endswith(".md") or path.startswith("docs/"):
        return False
    if path.startswith(("web/tests/", "web/e2e/", "scripts/tests/", "cloud/tests/")):
        return False
    name = path.rsplit("/", 1)[-1]
    if name.startswith("test_") or name.endswith("_test.py"):
        return False
    return path.startswith(RUNTIME_ROOTS)


def classify(subject: str, body: str, files: list[str]) -> str | None:
    if not any(is_runtime(path) for path in files):
        return None
    match = CONVENTIONAL.match(subject.strip())
    if (match and match.group("breaking")) or "BREAKING CHANGE" in subject or "BREAKING CHANGE" in body:
        return "major"
    if match and match.group("type") == "feat":
        return "minor"
    return "patch"


def apply_bump(version: tuple[int, int, int], kind: str | None) -> tuple[int, int, int]:
    major, minor, patch = version
    if kind is None:
        return version
    if kind == "major":
        return (major + 1, 0, 0)
    if kind == "minor":
        return (major, minor + 1, 0)
    if kind == "patch":
        return (major, minor, patch + 1)
    raise ValueError(kind)


def fold(commits: list[tuple[str, str, list[str]]], base: tuple[int, int, int] | None = None) -> tuple[int, int, int]:
    version = BASE_VERSION if base is None else base
    for subject, body, files in commits:
        version = apply_bump(version, classify(subject, body, files))
    return version


def read_version(path: Path) -> str:
    match = VERSION_KEY.search(path.read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"no version key in {path}")
    return match.group(2)


def write_version(path: Path, version: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, count = VERSION_KEY.subn(rf"\g<1>{version}\3", text, count=1)
    if count != 1:
        raise ValueError(f"no version key in {path}")
    path.write_text(updated, encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def load_commits(repo: Path, base: str) -> list[tuple[str, str, list[str]]]:
    subprocess.check_call(["git", "-C", str(repo), "cat-file", "-e", f"{base}^{{commit}}"])
    listed = _git(repo, "rev-list", "--no-merges", "--reverse", f"{base}..HEAD")
    commits: list[tuple[str, str, list[str]]] = []
    if not listed:
        return commits
    for sha in listed.splitlines():
        message = _git(repo, "show", "-s", "--format=%s%x00%b", sha)
        subject, _, body = message.partition("\0")
        files = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha)
        commits.append((subject, body, [line for line in files.splitlines() if line]))
    return commits


def expected_version(repo: Path) -> str:
    return format_version(fold(load_commits(repo, base_sha()), base_version()))


def git_sha(repo: Path) -> str:
    try:
        sha = _git(repo, "rev-parse", "--short=12", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return sha if re.fullmatch(r"[0-9a-f]{7,12}", sha) else "unknown"


def build_identity(repo: Path, channel: str) -> dict[str, str]:
    sha = git_sha(repo)
    try:
        # Rewritten history can leave an old object locally without making it
        # an ancestor. Such a fold is no more authoritative than missing history.
        _git(repo, "merge-base", "--is-ancestor", base_sha(), "HEAD")
        version = expected_version(repo)
        source = "history"
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        version = read_version(repo / "web" / "package.json")
        source = "package"
    built_at = ""
    if channel == "production" and sha != "unknown":
        built_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"version": version, "sha": sha, "builtAt": built_at, "channel": channel, "source": source}


def ci_version(repo: Path, expected_sha: str) -> str:
    """The exact build/tag label, including the existing no-history fallback."""
    if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise ValueError("release identity requires a full lowercase SHA")
    head = _git(repo, "rev-parse", "HEAD")
    if head != expected_sha:
        raise ValueError("release identity does not match the checked-out SHA")
    identity = build_identity(repo, "production")
    version = identity["version"]
    if format_version(parse_version(version)) != version:
        raise ValueError("release identity requires canonical semver")
    if identity["sha"] != expected_sha[:12]:
        raise ValueError("build identity does not match the checked-out SHA")
    return version


def check(repo: Path) -> str:
    expected = expected_version(repo)
    root_version = read_version(repo / "package.json")
    web_version = read_version(repo / "web" / "package.json")
    if root_version != web_version or root_version != expected:
        raise SystemExit(
            f"release version is {root_version} (web {web_version}); commits since {base_sha()[:12]} require {expected}"
        )
    return expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fold Radon semver from commits since 0.7.0.")
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ci-version", action="store_true", help="Validate the exact CI SHA and print the build/tag label.")
    parser.add_argument("--expected-sha", help="Exact full checked-out SHA required by --ci-version.")
    parser.add_argument("--channel", choices=("local", "production"), default="local")
    args = parser.parse_args(argv)
    repo = Path(args.repo)
    if args.ci_version:
        if not args.expected_sha or args.check or args.write or args.json:
            parser.error("--ci-version requires --expected-sha and cannot combine with another output mode")
        print(ci_version(repo, args.expected_sha))
        return 0
    if args.expected_sha:
        parser.error("--expected-sha requires --ci-version")
    if args.check:
        print(check(repo))
        return 0
    if args.write:
        version = expected_version(repo)
        write_version(repo / "package.json", version)
        write_version(repo / "web" / "package.json", version)
        print(version)
        return 0
    if args.json:
        print(json.dumps(build_identity(repo, args.channel)))
        return 0
    print(expected_version(repo))
    return 0


if __name__ == "__main__":
    sys.exit(main())
