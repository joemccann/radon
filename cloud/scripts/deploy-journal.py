#!/usr/bin/env python3
"""Atomic, fsynced state for a deploy transition that may outlive its process."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


FIELDS = ("requested_sha", "previous_sha", "stage_dir", "backup_dir", "phase")
PHASES = {
    "prepared",
    "backup_started",
    "backup_complete",
    "promoting",
    "target_unverified",
    "verified",
}


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_state(path: Path) -> dict[str, str]:
    state = json.loads(path.read_text(encoding="utf-8"))
    if state.get("version") != 1 or any(not isinstance(state.get(key), str) for key in FIELDS):
        raise ValueError("invalid transition journal schema")
    if state["phase"] not in PHASES:
        raise ValueError("invalid transition phase")
    return state


def write_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, **state}, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    if len(sys.argv) < 3:
        return 2
    command = sys.argv[1]
    path = Path(sys.argv[2])

    if command == "write" and len(sys.argv) == 8:
        state = dict(zip(FIELDS, sys.argv[3:], strict=True))
        if state["phase"] not in PHASES:
            return 2
        write_state(path, state)
        return 0
    if command == "phase" and len(sys.argv) == 4:
        state = read_state(path)
        if sys.argv[3] not in PHASES:
            return 2
        state["phase"] = sys.argv[3]
        write_state(path, state)
        return 0
    if command == "read" and len(sys.argv) == 3:
        state = read_state(path)
        for field in FIELDS:
            os.write(sys.stdout.fileno(), state[field].encode() + b"\0")
        return 0
    if command == "remove" and len(sys.argv) == 3:
        path.unlink(missing_ok=True)
        sync_directory(path.parent)
        return 0
    return 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"deploy journal error: {exc}", file=sys.stderr)
        raise SystemExit(1)
