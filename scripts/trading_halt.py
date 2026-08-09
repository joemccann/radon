#!/usr/bin/env python3
"""Trading halt flag — the kill switch's state (REL-004 / R-001).

One JSON file on local disk is the authority: every order-placing process
on the host (FastAPI routes, ib_place_order subprocess, the workflow
bridge, the exit-orders daemon handler, CLI tools) checks it before
transmitting anything to IB.

Semantics:
  - file absent            → trading allowed (normal state)
  - {"halted": true, ...}  → halted
  - {"halted": false, ...} → allowed (resume keeps an audit trail)
  - unreadable / malformed → HALTED. For a kill switch, "unreadable"
    must never mean "trading allowed".

Writes go through atomic_save so a crash mid-write cannot produce a
half-written flag that reads as allowed.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

HALT_FILE = Path(__file__).parent.parent / "data" / "trading_halt.json"


def _read_state() -> Optional[dict]:
    """Raw flag payload; None when the file is absent.

    Raises nothing: malformed content returns a sentinel halted state.
    """
    try:
        raw = HALT_FILE.read_text()
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 — unreadable flag = halted
        logger.error(f"trading_halt: flag unreadable ({exc}) — failing closed")
        return {"halted": True, "reason": f"halt flag unreadable: {exc}"}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError) as exc:
        logger.error(f"trading_halt: flag malformed ({exc}) — failing closed")
        return {"halted": True, "reason": f"halt flag malformed: {exc}"}
    if not isinstance(parsed, dict):
        return {"halted": True, "reason": "halt flag malformed: not an object"}
    return parsed


def is_trading_halted() -> bool:
    state = _read_state()
    return bool(state.get("halted")) if state else False


def get_halt_state() -> dict:
    """Current flag state for status surfaces (always a dict)."""
    state = _read_state()
    if not state:
        return {"halted": False}
    return state


def _write_state(state: dict) -> None:
    HALT_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        from utils.atomic_io import atomic_save

        atomic_save(str(HALT_FILE), state)
    except ImportError:
        HALT_FILE.write_text(json.dumps(state, indent=2))


def set_halt(reason: str, actor: str = "operator") -> dict:
    state = {
        "halted": True,
        "reason": reason or "manual halt",
        "actor": actor,
        "halted_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_state(state)
    logger.warning(f"TRADING HALTED by {actor}: {reason}")
    return state


def clear_halt(actor: str = "operator") -> dict:
    state = {
        "halted": False,
        "actor": actor,
        "resumed_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_state(state)
    logger.warning(f"Trading resumed by {actor}")
    return state
