"""PERF-011: incremental vol-cone must not re-upsert the whole history.

Checkpoints already persist each completed (ticker, expiry) delta. The
terminal `_mirror_snapshot` then flattened every series in the payload, so
one new session rewrote tens of thousands of `vol_cone_history` rows
(76_800 at universe cap, 192 Hrana INSERTs at the 400-row chunk). The
terminal write retries only the rows this run fetched.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import fetch_vol_cone as mod
from db import writer
from fetch_vol_cone import last_completed_session_date, select_target_expiries


def _history(n_tickers: int, n_sessions: int, *, skip: tuple[str, str, str] | None = None):
    now = datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc)
    last_completed = last_completed_session_date(now)
    expiries = select_target_expiries(date.fromisoformat(last_completed))
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    sessions = [
        (date(2026, 7, 30) + timedelta(days=i)).isoformat() for i in range(n_sessions - 1)
    ] + [last_completed]
    rows = [
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
        for ticker in tickers
        for expiry in expiries
        for session in sessions
        if skip is None or (ticker, session, expiry.isoformat()) != skip
    ]
    return rows, tickers, now, last_completed, expiries


def test_incremental_run_upserts_only_new_rows(tmp_path, monkeypatch):
    now = datetime(2026, 8, 12, 23, 0, tzinfo=timezone.utc)
    last_completed = last_completed_session_date(now)
    first_expiry = select_target_expiries(date.fromisoformat(last_completed))[0].isoformat()
    rows, tickers, now, last_completed, expiries = _history(
        5, 10, skip=("T000", last_completed, first_expiry)
    )
    new_row = {
        "ticker": "T000",
        "date": last_completed,
        "expiry": expiries[0].isoformat(),
        "dte": 37,
        "spot": 100.0,
        "atm_iv": 0.3,
        "call_10_iv": 0.25,
        "put_10_iv": 0.28,
        "call_10_strike": 110.0,
        "put_10_strike": 90.0,
    }
    upserted: list[dict] = []

    def _capture(batch, recorded_at=None):
        upserted.extend(batch)

    monkeypatch.setattr(mod, "VOL_CONE_JSON", tmp_path / "vol_cone.json")
    monkeypatch.setattr(mod, "_read_history_rows", lambda: rows)
    monkeypatch.setattr(mod, "_backfill_ticker", lambda *a, **k: [dict(new_row)])
    monkeypatch.setattr(writer, "ensure_no_replica_for_writers", lambda: None)
    monkeypatch.setattr(writer, "upsert_vol_cone_rows", _capture)
    monkeypatch.setattr(writer, "upsert_scan_snapshot", lambda *a, **k: None)
    monkeypatch.setattr(writer, "record_service_health", lambda *a, **k: None)

    payload = mod.run(client=object(), now=now, tickers=tickers)

    n_history = len(rows) + 1
    n_new = 1
    # Checkpoint + terminal retry of the same delta; never the full table.
    assert payload["count"] == 5 * len(expiries)
    assert len(upserted) <= 2 * n_new, (
        f"upserted {len(upserted)} rows for {n_new} new session; "
        f"history size {n_history} must not be rewritten"
    )
    assert { (r["ticker"], r["date"], r["expiry"]) for r in upserted } == {
        ("T000", last_completed, expiries[0].isoformat())
    }
