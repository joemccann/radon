"""PERF-010: EOD vol-cone must index history by (ticker, expiry) once.

`run()` used to scan every stored row for every target pair when checking
missing sessions and again when building series. At universe cap (120 names
x 8 monthlies x 80 sessions = 76_800 rows) that is 147_456_000 row visits
on the heartbeat path that fetches no UW data. Production `radon-vol-cone`
spent 168s CPU on 2026-10-14. The budget is linear in history size.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import fetch_vol_cone as mod
from fetch_vol_cone import last_completed_session_date, select_target_expiries


class _CountRow(dict):
    visits = 0

    def __getitem__(self, key):
        type(self).visits += 1
        return dict.__getitem__(self, key)


class _NoFetchClient:
    def fetch_greeks(self, *args, **kwargs):
        raise AssertionError("heartbeat must not fetch greeks")

    def fetch_closes(self, ticker):
        return {}

    def fetch_spot(self, ticker):
        return 100.0


def _complete_history(n_tickers: int, n_sessions: int) -> tuple[list[_CountRow], list[str], datetime]:
    now = datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc)
    last_completed = last_completed_session_date(now)
    expiries = select_target_expiries(date.fromisoformat(last_completed))
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    sessions = [
        (date(2026, 7, 30) + timedelta(days=i)).isoformat() for i in range(n_sessions - 1)
    ] + [last_completed]
    rows = [
        _CountRow(
            {
                "ticker": ticker,
                "date": session,
                "expiry": expiry.isoformat(),
                "dte": 37,
                "spot": 100.0,
                "atm_iv": 0.3,
                "call_10_iv": 0.25,
                "put_10_iv": 0.28,
                "call_10_strike": 110.0,
                "put_10_strike": 90.0,
            }
        )
        for ticker in tickers
        for expiry in expiries
        for session in sessions
    ]
    return rows, tickers, now


def test_heartbeat_row_visits_are_linear_in_history_size(tmp_path, monkeypatch):
    """Fails on the nested full-history scans; passes after a one-pass index."""
    rows, tickers, now = _complete_history(n_tickers=10, n_sessions=10)
    n_rows = len(rows)
    n_pairs = 10 * len(select_target_expiries(date(2026, 8, 12)))
    # Nested scans: 2 passes × pairs × rows × (ticker, expiry) gets ≈ 2*50*500*2.
    quadratic = 2 * n_pairs * n_rows
    linear_budget = n_rows * 20

    monkeypatch.setattr(mod, "VOL_CONE_JSON", tmp_path / "vol_cone.json")
    monkeypatch.setattr(mod, "_write_db_cache", lambda *a, **k: None)
    monkeypatch.setattr(mod, "_read_history_rows", lambda: rows)

    _CountRow.visits = 0
    payload = mod.run(client=_NoFetchClient(), now=now, tickers=tickers)

    assert payload["count"] == n_pairs
    assert _CountRow.visits < quadratic, (
        f"visits {_CountRow.visits} still in the nested-scan band {quadratic}"
    )
    assert _CountRow.visits <= linear_budget, (
        f"visits {_CountRow.visits} exceeded linear budget {linear_budget} "
        f"for {n_rows} history rows"
    )
