#!/usr/bin/env python3
"""Prune inactive immutable deploy runners under a serialized root lock."""

from __future__ import annotations

import argparse
import fcntl
import os
import re
import shutil
from pathlib import Path


RUNNER_NAME = re.compile(r"^[0-9a-f]{40}\.[0-9]+\.[0-9]+$")
INDEX_LOCK = ".index.lock"
ACTIVE_LOCK = ".active.lock"


def _remove(path: Path) -> None:
    if path.is_symlink() or not path.is_dir():
        path.unlink(missing_ok=True)
    else:
        shutil.rmtree(path)


def prune(root: Path, current: Path, keep_newest: int) -> tuple[int, int]:
    root = root.resolve(strict=True)
    current = current.resolve(strict=True)
    if current.parent != root or not RUNNER_NAME.fullmatch(current.name):
        raise ValueError("current runner must be a direct, named child of runner root")
    if keep_newest < 0:
        raise ValueError("keep-newest must be non-negative")

    lock_path = root / INDEX_LOCK
    lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    removed = 0
    active = 0
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)

        # Extraction is atomic and holds the same index lock, so hidden staging
        # directories here can only be leftovers from a terminated workflow.
        for entry in root.iterdir():
            if entry.name.startswith(".runner."):
                _remove(entry)
                removed += 1

        runners = [
            entry
            for entry in root.iterdir()
            if RUNNER_NAME.fullmatch(entry.name)
            and entry != current
            and entry.is_dir()
            and not entry.is_symlink()
        ]
        runners.sort(key=lambda entry: entry.stat().st_mtime_ns, reverse=True)

        for candidate in runners[keep_newest:]:
            candidate_lock = candidate / ACTIVE_LOCK
            candidate_fd = os.open(
                candidate_lock,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                0o600,
            )
            try:
                try:
                    fcntl.flock(candidate_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    active += 1
                    continue
                shutil.rmtree(candidate)
                removed += 1
            finally:
                os.close(candidate_fd)
    finally:
        os.close(lock_fd)

    return removed, active


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--current", required=True, type=Path)
    parser.add_argument("--keep-newest", required=True, type=int)
    args = parser.parse_args()

    removed, active = prune(args.root, args.current, args.keep_newest)
    print(f"deploy runner retention: removed={removed} active_skipped={active}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
