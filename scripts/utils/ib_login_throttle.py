"""IBKR login-throttle quiet period, shared by the watchdog and the broker daemon.

Stdlib only: the broker's Gateway-control daemon runs under the system
interpreter and must not import the watchdog.

While IBKR answers Gateway logins with "Too many failed login attempts", every
further login attempt keeps its failed-login counter armed. The only cure is a
quiet period with no attempts. The watchdog records when the throttle was
seen; the quiet period doubles with each retry the watchdog itself spends.
"""
from __future__ import annotations

import json
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
    """Epoch seconds before which no login may be attempted, or None when the
    watchdog has recorded no throttle (or its state cannot be read)."""
    try:
        state = json.loads(Path(state_path).read_text())
        since = float(state.get("login_throttle_since") or 0.0)
        retries = int(state.get("login_throttle_retries") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    if since <= 0:
        return None
    return since + login_throttle_cooldown(retries)


def format_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M UTC")
