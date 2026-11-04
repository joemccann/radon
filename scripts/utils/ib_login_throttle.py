"""IBKR login-throttle quiet period, shared by the watchdog and the broker daemon.

Stdlib only: the broker's Gateway-control daemon runs under the system
interpreter and must not import the watchdog.

While IBKR answers Gateway logins with "Too many failed login attempts", every
further login attempt keeps its failed-login counter armed. The only cure is a
quiet period with no attempts. The watchdog records when the throttle was
seen; the quiet period doubles with each retry the watchdog itself spends.

A missing state file means no episode (logins allowed). An unreadable or
corrupt one fails closed for one base quiet period (see
``login_throttle_retry_at``), like every other Gateway auth guard.
"""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

LOGIN_THROTTLE_COOLDOWN_BASE_SECS = 900
LOGIN_THROTTLE_COOLDOWN_CAP_SECS = 3600


def login_throttle_cooldown(retries: int) -> float:
    return min(
        LOGIN_THROTTLE_COOLDOWN_BASE_SECS * (2 ** retries),
        LOGIN_THROTTLE_COOLDOWN_CAP_SECS,
    )


def login_throttle_retry_at(state_path: Path) -> float | None:
    """Epoch seconds before which no login may be attempted, or None."""
    return login_throttle_gate(state_path)[0]


def login_throttle_gate(state_path: Path) -> tuple[float | None, bool]:
    """``(retry_at, state_unreadable)``: ``retry_at`` as for
    ``login_throttle_retry_at``; ``state_unreadable`` is True when the block
    comes from fail-closed unreadable state rather than a recorded throttle.

    Three readings, never conflated:
      - file missing: the watchdog never recorded an episode -> None (open).
      - readable, no ``login_throttle_since``: no episode -> None (open).
      - unreadable or corrupt (I/O error, bad JSON, not an object, mistyped
        field): it may be hiding a live throttle, and every extra login
        re-arms IBKR's failed-login counter, so fail CLOSED for one base
        quiet period from the file's mtime (now, if even stat fails). The
        window is bounded: a file left corrupt stops blocking after that, and
        the watchdog rewrites it every cycle.
    """
    path = Path(state_path)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None, False
    except (OSError, UnicodeDecodeError):
        return _unreadable_retry_at(path), True
    try:
        state = json.loads(raw)
        if not isinstance(state, dict):
            raise TypeError("watchdog state is not an object")
        since = _number(state.get("login_throttle_since"))
        retries = _number(state.get("login_throttle_retries"))
    except (ValueError, TypeError):
        return _unreadable_retry_at(path), True
    if since <= 0:
        return None, False
    return since + login_throttle_cooldown(max(0, int(retries))), False


def _number(value: object) -> float:
    """``None`` reads as 0; any other non-number is corrupt state."""
    if value is None:
        return 0.0
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise TypeError(f"not a number: {value!r}")
    return float(value)


def _unreadable_retry_at(path: Path) -> float:
    try:
        modified_at = path.stat().st_mtime
    except OSError:
        modified_at = time.time()
    return modified_at + LOGIN_THROTTLE_COOLDOWN_BASE_SECS


def format_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M UTC")
