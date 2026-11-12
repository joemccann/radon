"""Pure merge logic for the IBKR calendar accumulator.

The IB fetch itself needs a live gateway and is verified live, not in unit tests.
"""
from __future__ import annotations

import json
import sys
import types

import pytest

import fetch_market_calendar as fmc
from fetch_market_calendar import merge_days


def test_merge_overrides_existing_and_keeps_accumulated():
    existing = {
        "2026-06-15": {"status": "open", "open_et": "09:30", "close_et": "16:00"},
        "2026-06-19": {"status": "open", "open_et": "09:30", "close_et": "16:00"},
    }
    new = {
        "2026-06-19": {"status": "closed", "open_et": None, "close_et": None},
        "2026-06-22": {"status": "open", "open_et": "09:30", "close_et": "16:00"},
    }
    merged = merge_days(existing, new)
    # Sorted by date.
    assert list(merged.keys()) == ["2026-06-15", "2026-06-19", "2026-06-22"]
    # IBKR (new) wins for the overlapping date.
    assert merged["2026-06-19"]["status"] == "closed"
    # Older accumulated date preserved.
    assert merged["2026-06-15"]["status"] == "open"


def test_merge_into_empty():
    assert merge_days({}, {"2026-06-22": {"status": "open"}}) == {
        "2026-06-22": {"status": "open"}
    }


# D13: radon-market-calendar.timer runs this script weekly. Every run must
# leave a `market-calendar` service_health row so the watchdog catalogs can
# tell a missed or failing week from a healthy one.

@pytest.fixture
def health(monkeypatch):
    rows: list[tuple[str, str]] = []
    writer = types.SimpleNamespace(
        ensure_no_replica_for_writers=lambda: None,
        record_service_health=lambda name, state, **_: rows.append((name, state)),
    )
    monkeypatch.setitem(sys.modules, "db.writer", writer)
    monkeypatch.setitem(sys.modules, "db", types.SimpleNamespace(writer=writer))
    monkeypatch.setattr(sys, "argv", ["fetch_market_calendar.py"])
    return rows


def test_successful_run_records_an_ok_heartbeat(health, monkeypatch, capsys):
    monkeypatch.setattr(fmc, "fetch_liquid_hours", lambda symbol, port: ("raw", "US/Eastern"))
    monkeypatch.setattr(fmc, "parse_liquid_hours", lambda raw: {"2026-11-16": {"status": "open"}})
    monkeypatch.setattr(fmc, "_read_existing_days", lambda: {})
    monkeypatch.setattr(fmc, "atomic_save", lambda path, payload: None)

    fmc.main()

    assert json.loads(capsys.readouterr().out.strip())["ok"] is True
    assert health == [("market-calendar", "ok")]


def test_failed_run_records_an_error_heartbeat_and_exits_1(health, monkeypatch):
    def unauthenticated(symbol, port):
        raise RuntimeError("gateway auth_state=pending (not authenticated)")

    monkeypatch.setattr(fmc, "fetch_liquid_hours", unauthenticated)

    with pytest.raises(SystemExit) as exit_info:
        fmc.main()

    assert exit_info.value.code == 1
    assert health == [("market-calendar", "error")]
