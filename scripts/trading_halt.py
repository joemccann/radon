#!/usr/bin/env python3
"""Trading halt flag — the kill switch's state (REL-004 / R-001).

One JSON file on local disk is the authority: every order-placing process
on the host (FastAPI routes, ib_place_order subprocess, the workflow
bridge, the exit-orders daemon handler, CLI tools) checks it before
transmitting anything to IB.

Semantics (HaltStatus):
  - ABSENT     file absent               → trading allowed (normal state)
  - HALTED     {"halted": true, ...}     → halted
  - RESUMED    {"halted": false, ...}    → allowed (resume keeps an audit trail)
  - MALFORMED  an object whose `halted` is not a boolean, `{}` included
                                         → HALTED
  - UNREADABLE unreadable / not JSON / not an object → HALTED.
For a kill switch, anything short of a proven `"halted": false` must never
mean "trading allowed".

Every halt carries a `halt_id`. `clear_halt(halt_id=...)` is compare-and-set:
a resume issued against the halt an operator SAW cannot clear a newer halt
another session armed since (HaltConflict). Set and clear run under an
exclusive flock, and every write is temp + os.replace, so a crash mid-write
cannot produce a half-written flag that reads as allowed.
"""

from __future__ import annotations

import enum
import fcntl
import json
import logging
import os
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

HALT_FILE = Path(__file__).parent.parent / "data" / "trading_halt.json"


class HaltStatus(enum.Enum):
    ABSENT = "absent"
    HALTED = "halted"
    RESUMED = "resumed"
    MALFORMED = "malformed"
    UNREADABLE = "unreadable"

    @property
    def blocks_trading(self) -> bool:
        return self not in (HaltStatus.ABSENT, HaltStatus.RESUMED)


class HaltConflict(RuntimeError):
    """clear_halt named a halt that is no longer the live one."""


def _read() -> tuple[HaltStatus, dict]:
    """The one classifier. Never raises; every unproven shape blocks."""
    try:
        raw = HALT_FILE.read_text()
    except FileNotFoundError:
        return HaltStatus.ABSENT, {"halted": False}
    except Exception as exc:  # noqa: BLE001 — unreadable flag = halted
        logger.error(f"trading_halt: flag unreadable ({exc}) — failing closed")
        return HaltStatus.UNREADABLE, {"halted": True, "reason": f"halt flag unreadable: {exc}"}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.error(f"trading_halt: flag malformed ({exc}) — failing closed")
        return HaltStatus.UNREADABLE, {"halted": True, "reason": f"halt flag malformed: {exc}"}
    if not isinstance(parsed, dict):
        return HaltStatus.UNREADABLE, {"halted": True, "reason": "halt flag malformed: not an object"}
    halted = parsed.get("halted")
    if not isinstance(halted, bool):
        logger.error("trading_halt: flag has no boolean `halted` — failing closed")
        return HaltStatus.MALFORMED, {
            **parsed,
            "halted": True,
            "reason": "halt flag malformed: no boolean `halted`",
        }
    return (HaltStatus.HALTED if halted else HaltStatus.RESUMED), parsed


def read_halt_status() -> HaltStatus:
    return _read()[0]


def is_trading_halted() -> bool:
    return _read()[0].blocks_trading


def get_halt_state() -> dict:
    """Current flag state for status surfaces (always a dict)."""
    return _read()[1]


@contextmanager
def _exclusive() -> Iterator[None]:
    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HALT_FILE.with_name(HALT_FILE.name + ".lock"), "a") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_state(state: dict) -> None:
    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        from utils.atomic_io import atomic_save
    except ImportError:
        atomic_save = None
    if atomic_save is not None:
        atomic_save(str(HALT_FILE), state)
        return
    fd, tmp = tempfile.mkstemp(dir=str(HALT_FILE.parent), prefix=f".{HALT_FILE.name}.")
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(state, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, str(HALT_FILE))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_halt(reason: str, actor: str = "operator") -> dict:
    state = {
        "halted": True,
        "halt_id": uuid.uuid4().hex,
        "reason": reason or "manual halt",
        "actor": actor,
        "halted_at": datetime.now(timezone.utc).isoformat(),
    }
    with _exclusive():
        _write_state(state)
    logger.warning(f"TRADING HALTED by {actor}: {reason}")
    return state


def clear_halt(actor: str = "operator", halt_id: Optional[str] = None) -> dict:
    """Resume trading. With `halt_id`, only if that halt is still the live one.

    Without it the resume is unconditional (legacy callers). A MALFORMED or
    UNREADABLE flag has no id to match, so a CAS resume refuses it too.
    """
    with _exclusive():
        status, current = _read()
        if halt_id is not None and status.blocks_trading and current.get("halt_id") != halt_id:
            raise HaltConflict(
                f"halt {halt_id} is not the live halt ({current.get('halt_id') or status.value})"
            )
        state = {
            "halted": False,
            "actor": actor,
            "resumed_at": datetime.now(timezone.utc).isoformat(),
        }
        if current.get("halt_id"):
            state["halt_id"] = current["halt_id"]
        _write_state(state)
    logger.warning(f"Trading resumed by {actor}")
    return state
