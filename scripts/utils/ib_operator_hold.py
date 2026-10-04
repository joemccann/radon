#!/usr/bin/env python3
"""Operator hold: while set, nothing may log Radon's IB Gateway in to IBKR.

The Gateway and the operator share one IBKR username, and IBKR allows one
session per username. On 2026-09-25 IBC (``ExistingSessionDetectedAction=
primary``) reclaimed the session three times and kicked the operator off
interactivebrokers.com. ``radon ib release`` (or the admin panel, or the
watchdog when IBC reports another session took the login) sets this hold;
every start path refuses while it is set; ``radon ib resume`` or the admin
panel clears it. An optional ``expires_at`` is a reminder only: a past expiry
reads ``expired`` but stays HELD, because an expiring hold logs the Gateway
back in and kicks the operator, which is the bug.

Non-root writers (the broker daemon and the watchdog run as radon) can set a
hold: their flag is not root-owned, so it reads HELD by the rule below, and its
reason and actor still show. They clear by removing the flag (absent = not
held) because only root can write a trusted not-held flag.

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


def _parse_detail(raw: str) -> dict:
    try:
        detail = json.loads(raw)
    except ValueError:
        return {}
    return detail if isinstance(detail, dict) else {}


def _with_expiry(state: dict) -> dict:
    """Mark a past ``expires_at``. Display only: it never clears the hold."""
    raw = state.get("expires_at")
    if not raw:
        return state
    try:
        expires = datetime.fromisoformat(str(raw))
    except ValueError:
        return state
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return {**state, "expired": datetime.now(timezone.utc) >= expires}


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
    try:
        # REL-299 / R-718: only the ASCII prefix authorizes a clear flag.
        # Corrupt UTF-8 in diagnostic fields must not crash every reader.
        # Replacement preserves the prefix decision used by the root shim.
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return _untrusted(str(exc))
    detail = _parse_detail(raw)
    if info.st_uid != _owner_uid():
        # Held whatever it says. A hold a radon process set still shows who
        # and why; a not-held flag from anyone but root is the forgery.
        if detail.get("held") is True:
            return _with_expiry({**detail, "held": True, "trusted": False,
                                 "reason": detail.get("reason") or "hold flag set"})
        return _untrusted(f"owned by uid {info.st_uid}")
    if not raw.startswith(NOT_HELD_PREFIX):
        return _with_expiry({**detail, "held": True, "reason": detail.get("reason") or "hold flag set"})
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


def set_hold(reason: str, actor: str, expires_at: str | None = None) -> dict:
    state = {
        "held": True,
        "reason": reason or "operator release",
        "actor": actor,
        "held_at": _now(),
        "id": f"hold-{uuid.uuid4()}",
    }
    if expires_at:
        parsed = datetime.fromisoformat(expires_at)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        state["expires_at"] = parsed.astimezone(timezone.utc).isoformat()
    _write_flag(state)
    _audit("hold", state)
    return state


def clear_hold(actor: str) -> dict:
    state = {"held": False, "actor": actor, "cleared_at": _now()}
    if os.geteuid() == _owner_uid():
        _write_flag(state)
    else:
        # Only the owner (root) can write a not-held flag that reads as one.
        _hold_path().unlink(missing_ok=True)
    _audit("clear", state)
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="IBKR operator hold")
    verbs = parser.add_subparsers(dest="verb", required=True)
    verbs.add_parser("status", help=f"print the hold; exit {HELD_RC} when held")
    hold = verbs.add_parser("hold")
    hold.add_argument("--reason", default="operator release")
    hold.add_argument("--actor", required=True)
    hold.add_argument("--expires-at", default=None,
                      help="ISO time; a reminder only, the hold never lifts itself")
    clear = verbs.add_parser("clear")
    clear.add_argument("--actor", required=True)
    args = parser.parse_args(argv)

    if args.verb == "hold":
        try:
            state = set_hold(args.reason, args.actor, args.expires_at)
        except ValueError as exc:
            print(f"ib_operator_hold: bad --expires-at: {exc}", file=sys.stderr)
            return 64
        print(json.dumps(state))
        return 0
    if args.verb == "clear":
        print(json.dumps(clear_hold(args.actor)))
        return 0
    state = hold_state()
    print(json.dumps(state))
    return HELD_RC if state["held"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
