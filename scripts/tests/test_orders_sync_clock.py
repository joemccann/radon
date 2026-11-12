"""The orders snapshot clock is the last confirmed sync, not the last fill."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from utils.orders_sync_clock import resolve_orders_last_sync  # noqa: E402

_FILL = "2026-11-12T13:41:06Z"
_SYNC = "2026-11-12T15:26:10Z"


def test_ok_clock_replaces_fill_time_on_an_empty_book():
    assert resolve_orders_last_sync(
        health_state="ok",
        health_finished_at=_SYNC,
        latest_open_updated_at="",
        latest_fill_time=_FILL,
    ) == _SYNC


def test_warn_clock_does_not_replace_fill_time():
    assert resolve_orders_last_sync(
        health_state="warn",
        health_finished_at=_SYNC,
        latest_open_updated_at="",
        latest_fill_time=_FILL,
    ) == _FILL


def test_naive_ok_clock_does_not_replace_fill_time():
    assert resolve_orders_last_sync(
        health_state="ok",
        health_finished_at="2026-11-12T15:26:10",
        latest_open_updated_at="",
        latest_fill_time=_FILL,
    ) == _FILL


def test_newer_open_updated_at_beats_an_older_ok_clock():
    newer = "2026-11-12T15:30:00Z"
    assert resolve_orders_last_sync(
        health_state="ok",
        health_finished_at=_SYNC,
        latest_open_updated_at=newer,
        latest_fill_time=_FILL,
    ) == newer


def test_build_orders_data_stamps_utc_z():
    import ib_orders

    stamped = ib_orders.build_orders_data([], [])["last_sync"]
    assert stamped.endswith("Z")
    parsed = datetime.fromisoformat(stamped.replace("Z", "+00:00"))
    skew = abs((datetime.now(timezone.utc) - parsed).total_seconds())
    assert skew < 5
