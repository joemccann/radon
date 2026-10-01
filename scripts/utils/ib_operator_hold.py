#!/usr/bin/env python3
"""Operator hold: while set, nothing may log Radon's IB Gateway in to IBKR.

The Gateway and the operator share one IBKR username, and IBKR allows one
session per username. On 2026-09-25 IBC (``ExistingSessionDetectedAction=
primary``) reclaimed the session three times and kicked the operator off
interactivebrokers.com. ``radon ib release`` sets this hold and stops the
Gateway; every start path refuses while it is set; only ``radon ib resume``
clears it. It never expires: an expiring hold logs the Gateway back in and
kicks the operator, which is the bug.

Semantics (fail-safe = HELD):
  - file absent                                  -> not held
  - regular file, owned by OWNER_UID, beginning
    with NOT_HELD_PREFIX                         -> not held
  - anything else (held flag, malformed, symlink,
    wrong owner, unreadable)                     -> HELD

The prefix rule, not a JSON parse, decides: the root docker shim enforces the
same rule in bash, and the two readers must never disagree. Writes are atomic
and always canonical, so a flag this module writes reads the same in both.

Stdlib only: the broker runs it under /usr/bin/python3.13, like ib_2fa_lock.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_HOLD_PATH = "/var/lib/radon/ib-operator-hold.json"
DEFAULT_AUDIT_PATH = "/var/log/radon/ib-operator-hold.jsonl"
NOT_HELD_PREFIX = '{"held": false'
HELD_RC = 73
FLAG_MODE = 0o644


def _hold_path() -> Path:
    return Path(os.environ.get("RADON_IB_OPERATOR_HOLD_PATH", DEFAULT_HOLD_PATH))


def _audit_path() -> Path:
    return Path(os.environ.get("RADON_IB_OPERATOR_HOLD_AUDIT", DEFAULT_AUDIT_PATH))


def _owner_uid() -> int:
    return int(os.environ.get("RADON_IB_OPERATOR_HOLD_OWNER_UID", "0"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _untrusted(reason: str) -> dict:
    return {"held": True, "reason": f"hold flag untrusted: {reason}"}


def hold_state() -> dict:
    """Current hold for status surfaces; always a dict with a bool ``held``."""
    path = _hold_path()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"held": False}
    except OSError as exc:
        return _untrusted(str(exc))
    if not stat.S_ISREG(info.st_mode):
        return _untrusted("not a regular file")
    if info.st_uid != _owner_uid():
        return _untrusted(f"owned by uid {info.st_uid}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return _untrusted(str(exc))
    try:
        detail = json.loads(raw)
    except ValueError:
        detail = {}
    if not isinstance(detail, dict):
        detail = {}
    if not raw.startswith(NOT_HELD_PREFIX):
        return {**detail, "held": True, "reason": detail.get("reason") or "hold flag set"}
    return {**detail, "held": False}


def is_held() -> bool:
    return bool(hold_state()["held"])


def _write_flag(state: dict) -> None:
    path = _hold_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ib-operator-hold.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), FLAG_MODE)
            handle.write(json.dumps(state) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _audit(event: str, state: dict) -> None:
    """Best effort: an audit failure must never block a release."""
    entry = {"event": event, "at": _now(), **state}
    try:
        path = _audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry) + "\n")
    except OSError as exc:
        print(f"ib_operator_hold: audit not written ({exc})", file=sys.stderr)


def set_hold(reason: str, actor: str) -> dict:
    state = {
        "held": True,
        "reason": reason or "operator release",
        "actor": actor,
        "held_at": _now(),
        "id": f"hold-{uuid.uuid4()}",
    }
    _write_flag(state)
    _audit("hold", state)
    return state


def clear_hold(actor: str) -> dict:
    state = {"held": False, "actor": actor, "cleared_at": _now()}
    _write_flag(state)
    _audit("clear", state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IBKR operator hold")
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("status", help=f"print the hold; exit {HELD_RC} when held")
    hold = verbs.add_parser("hold")
    hold.add_argument("--reason", default="operator release")
    hold.add_argument("--actor", required=True)
    clear = verbs.add_parser("clear")
    clear.add_argument("--actor", required=True)
    args = parser.parse_args(argv)

    if args.verb == "hold":
        print(json.dumps(set_hold(args.reason, args.actor)))
        return 0
    if args.verb == "clear":
        print(json.dumps(clear_hold(args.actor)))
        return 0
    state = hold_state()
    print(json.dumps(state))
    return HELD_RC if state["held"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
