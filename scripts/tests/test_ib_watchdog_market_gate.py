"""Market-hours gate on the watchdog's 2FA-chasing restarts (2026-06-21).

IBKR's weekend session expiry left the gateway parked at ``awaiting_2fa``; the
stuck-2FA self-heal restarted it ~51x (each a fresh 2FA push) because it ran
24/7 with no market awareness. ``data_plane_window_active`` gates restarts to
trading-day pre-open->close hours; off-hours (weekends/holidays/overnight) the
existing quiet-window freeze path suppresses the push.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

import ib_watchdog  # type: ignore[import-not-found]
from ib_watchdog import (  # type: ignore[import-not-found]
    DEFAULT_STUCK_2FA_THRESHOLD,
    GatewayState,
    data_plane_window_active,
    run_cycle,
)


@pytest.fixture(autouse=True)
def _redirect_2fa_lock_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_2FA_LOCK_PATH", str(tmp_path / "ib-2fa-push-lock.json"))


@pytest.fixture
def state_path(tmp_path: Path) -> Path:
    return tmp_path / "watchdog-state.json"


def _utc(y: int, m: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


# ── data_plane_window_active (the pure gate) ─────────────────────────────


class TestDataPlaneWindow:
    def test_weekend_is_closed(self):
        assert data_plane_window_active(_utc(2026, 6, 21, 17)) is False  # Sun 13:00 ET
        assert data_plane_window_active(_utc(2026, 6, 20, 17)) is False  # Sat

    def test_holiday_weekday_is_closed(self):
        # 2026-06-19 Juneteenth (Fri), 13:00 ET == 17:00 UTC.
        assert data_plane_window_active(_utc(2026, 6, 19, 17)) is False

    def test_trading_day_in_window_is_open(self):
        # 2026-06-18 Thu, 11:00 ET == 15:00 UTC (inside 07:30-16:00).
        assert data_plane_window_active(_utc(2026, 6, 18, 15)) is True

    def test_trading_day_before_warmup_closed(self):
        # 06:00 ET == 10:00 UTC, before the 07:30 warmup start.
        assert data_plane_window_active(_utc(2026, 6, 18, 10)) is False

    def test_trading_day_after_close_closed(self):
        # 17:00 ET == 21:00 UTC, after the 16:00 close.
        assert data_plane_window_active(_utc(2026, 6, 18, 21)) is False

    def test_custom_window_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("RADON_GW_2FA_CHASE_WINDOW_ET", "04:00-20:00")
        assert data_plane_window_active(_utc(2026, 6, 18, 10)) is True  # 06:00 ET now inside

    def test_fails_open_on_error(self, monkeypatch: pytest.MonkeyPatch):
        # A calendar hiccup must preserve recovery (return True), never silently
        # freeze the gateway un-recovered at the open.
        from utils import market_calendar

        def _boom(*_a, **_k):
            raise RuntimeError("calendar unavailable")

        monkeypatch.setattr(market_calendar, "_is_trading_day", _boom)
        assert data_plane_window_active(_utc(2026, 6, 21, 17)) is True  # Sunday, but errors → open


# ── run_cycle: stuck-2FA freezes off-hours, fires in-window ──────────────

_STUCK_2FA = {
    "ib_gateway": {
        "service_state": "healthy",
        "port_listening": True,
        "upstream_dead": False,
        "auth_state": "awaiting_2fa",
    }
}


def _drive(state_path: Path, when: datetime, cycles: int):
    with (
        patch("ib_watchdog.fetch_health", side_effect=lambda u, t: GatewayState.from_health_payload(_STUCK_2FA)),
        patch("ib_watchdog.trigger_restart", return_value=True) as restart_mock,
        patch("ib_watchdog.record_service_health"),
        patch("ib_watchdog.probe_gateway_direct", return_value="unknown"),
        patch("ib_watchdog.attribute_api_down", return_value="attribution_unavailable"),
    ):
        result = None
        for _ in range(cycles):
            result = run_cycle(state_path=state_path, dry_run=True, utcnow=lambda: when)
    return result, restart_mock


class TestRunCycleMarketFreeze:
    def test_sunday_stuck_2fa_never_restarts(self, state_path: Path):
        # 2026-06-21 Sunday 13:00 ET — market closed, not in a quiet window.
        result, restart = _drive(state_path, _utc(2026, 6, 21, 17), cycles=DEFAULT_STUCK_2FA_THRESHOLD + 3)
        restart.assert_not_called()
        assert "frozen" in result.last_outcome

    def test_trading_day_stuck_2fa_does_restart(self, state_path: Path):
        # 2026-06-18 Thu 11:00 ET — in window, so the gate allows the push.
        _result, restart = _drive(state_path, _utc(2026, 6, 18, 15), cycles=DEFAULT_STUCK_2FA_THRESHOLD + 1)
        assert restart.called
