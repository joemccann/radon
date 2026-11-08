"""USI ASK-BID trade-count imbalance: units, sign, EMA10, gaps, persistence.

Captured fixture is official TradingView MCP history labeled as such, never a
production Turso row. Screenshot prints are not stored as fake data.
"""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

import fetch_imbalance as scan

NOW = datetime(2026, 11, 6, 23, tzinfo=timezone.utc)
FIXTURE = Path(__file__).parent / "fixtures" / "imbalance_sample.json"


def timestamp(day, hour=13):
    return int(datetime.fromisoformat(day).replace(hour=hour, minute=30, tzinfo=timezone.utc).timestamp())


def sources(days=("2026-11-05", "2026-11-06"), *, ask=100, bid=40, batd=None, spx=7376.86):
    values = {
        "ask": ask, "bid": bid,
        "batd": ask - bid if batd is None else batd,
        "spx": spx,
    }
    return {
        field: {
            "symbol": symbol, "interval": "1D", "history_complete": False,
            "fetched_at": NOW.isoformat(),
            "bars": [{"timestamp": timestamp(day), "close": values[field]} for day in days],
        }
        for field, symbol in scan.SYMBOLS.items()
    }


def test_units_are_trade_counts_not_share_volume():
    payload = scan.build_output(sources(), now=NOW)
    source = payload["source"]
    assert source["units"] == "trade_count"
    assert source["units_verified"] is True
    assert "USI:VOL.ASK.US" in source["share_volume_symbols_not_used"]
    assert source["universe_verified"] is False
    assert source["history_complete"] is False
    assert payload["current"]["imbalance"] == 60
    assert payload["current"]["ask"] == 100
    assert payload["current"]["bid"] == 40


def test_sign_is_ask_minus_bid_never_batd_substitute():
    data = sources(batd=99)
    payload = scan.build_output(data, now=NOW)
    assert payload["current"]["imbalance"] == 60
    assert payload["current"]["batd"] == 99
    assert payload["source"]["parity_mismatches"][-1]["date"] == "2026-11-06"


def test_captured_fixture_last_bar_matches_live_mcp_arithmetic():
    raw = json.loads(FIXTURE.read_text())
    wrapped = {
        field: {
            "symbol": symbol, "interval": "1D", "history_complete": False,
            "fetched_at": raw["captured_at"],
            "bars": raw["symbols"][symbol]["bars"],
        }
        for field, symbol in scan.SYMBOLS.items()
    }
    payload = scan.build_output(wrapped, now=NOW)
    assert payload["current"]["date"] == "2026-11-06"
    assert payload["current"]["ask"] == 13_124_065
    assert payload["current"]["bid"] == 12_215_551
    assert payload["current"]["imbalance"] == 908_514
    assert payload["current"]["batd"] == 908_514
    assert payload["current"]["spx_close"] == 7376.86
    assert payload["current"]["date"] not in {row["date"] for row in payload["source"]["parity_mismatches"]}
    assert payload["source"]["history_complete"] is False
    # Historical USI BATD can disagree with ASK-BID; published value stays ASK-BID.


def test_ema10_sma_seed_then_k_two_over_eleven():
    days = [f"2026-10-{i:02d}" for i in range(20, 32)]  # 12 weekdays? use sequential dates
    days = [f"2026-10-{n:02d}" for n in range(6, 18)]  # 12 calendar days
    data = sources(tuple(days), ask=10, bid=0)
    for i, bar in enumerate(data["ask"]["bars"]):
        bar["close"] = i + 1
        data["bid"]["bars"][i]["close"] = 0
        data["batd"]["bars"][i]["close"] = i + 1
    payload = scan.build_output(data, now=datetime(2026, 10, 17, 23, tzinfo=timezone.utc))
    series = payload["series"]
    assert all(row["ema10"] is None for row in series[:9])
    assert series[9]["ema10"] == pytest.approx(5.5)
    k = 2 / 11
    assert series[10]["ema10"] == pytest.approx((11 - 5.5) * k + 5.5)
    assert series[11]["imbalance"] == 12


def test_ema10_skips_gaps_and_does_not_treat_missing_as_zero():
    days = tuple(f"2026-10-{n:02d}" for n in range(6, 17))
    data = sources(days, ask=10, bid=0)
    for i, bar in enumerate(data["ask"]["bars"]):
        bar["close"] = i + 1
        data["bid"]["bars"][i]["close"] = 0
        data["batd"]["bars"][i]["close"] = i + 1
    data["ask"]["bars"][4]["close"] = None
    payload = scan.build_output(data, now=datetime(2026, 10, 16, 23, tzinfo=timezone.utc))
    assert payload["series"][4]["imbalance"] is None
    assert payload["series"][4]["ema10"] is None
    finite = [row["ema10"] for row in payload["series"] if row["ema10"] is not None]
    # Gap skipped: seed is 1,2,3,4,6,7,8,9,10,11. Treating the gap as 0 would seed 5.0.
    assert finite[0] == pytest.approx((1 + 2 + 3 + 4 + 6 + 7 + 8 + 9 + 10 + 11) / 10)


def test_gap_is_null_not_zero_or_forward_filled():
    data = sources()
    data["bid"]["bars"].pop()
    payload = scan.build_output(data, now=NOW)
    assert payload["current"]["date"] == "2026-11-05"
    assert payload["series"][-1]["bid"] is None
    assert payload["series"][-1]["imbalance"] is None
    assert payload["series"][-1]["ema10"] is None
    assert payload["degraded"] and payload["source"]["lagging"]
    assert payload["source"]["missing_sessions"] == [{"date": "2026-11-06", "fields": ["bid"]}]


def test_zero_trade_counts_are_valid():
    data = sources(ask=0, bid=0)
    payload = scan.build_output(data, now=NOW)
    assert payload["current"]["ask"] == 0
    assert payload["current"]["imbalance"] == 0


def test_negative_imbalance_is_valid():
    payload = scan.build_output(sources(ask=10, bid=40), now=NOW)
    assert payload["current"]["imbalance"] == -30


@pytest.mark.parametrize("field,value", [
    ("ask", -1), ("bid", 1.5), ("ask", True),
    ("spx", float("nan")), ("spx", float("inf")), ("spx", -1),
    ("ask", 0.01),
])
def test_invalid_close_units_quarantined(field, value):
    data = sources()
    data[field]["bars"][-1]["close"] = value
    payload = scan.build_output(data, now=NOW)
    assert payload["series"][-1][field if field != "spx" else "spx_close"] is None
    if field in {"ask", "bid"}:
        assert payload["series"][-1]["imbalance"] is None
        assert payload["degraded"]
    assert payload["source"]["invalid_observations"][-1]["field"] == field


@pytest.mark.parametrize("mutation", ["symbol", "interval", "empty", "duplicate", "future"])
def test_bad_source_identity_or_sessions_refused(mutation):
    data = sources()
    if mutation == "symbol":
        data["ask"]["symbol"] = "USI:VOL.ASK.US"
    elif mutation == "interval":
        data["ask"]["interval"] = "1W"
    elif mutation == "empty":
        data["ask"]["bars"] = []
    elif mutation == "duplicate":
        data["ask"]["bars"].append(copy.deepcopy(data["ask"]["bars"][-1]))
    else:
        data["ask"]["bars"][-1]["timestamp"] = timestamp("2026-11-09")
    with pytest.raises(ValueError):
        scan.build_output(data, now=NOW)


def test_cutoff_excludes_current_before_18_et():
    now = datetime(2026, 11, 6, 22, 59, tzinfo=timezone.utc)
    payload = scan.build_output(sources(), now=now)
    assert payload["data_date"] == "2026-11-05"
    assert not payload["degraded"]


def test_weekend_reuses_last_completed_session():
    now = datetime(2026, 11, 7, 23, tzinfo=timezone.utc)
    assert not scan.build_output(sources(), now=now)["degraded"]


def test_unjoined_histories_are_missing():
    data = sources()
    data["ask"]["bars"] = [{"timestamp": timestamp("2026-11-04"), "close": 2}]
    payload = scan.build_output(data, now=NOW)
    assert payload["missing"] and payload["current"] is None
    assert payload["data_date"] is None


def test_naive_collection_time_refused():
    with pytest.raises(ValueError):
        scan.build_output(sources(), now=NOW.replace(tzinfo=None))


def test_collect_exact_bounded_symbols_with_injected_client():
    class Client:
        def __init__(self):
            self.calls = []
        async def get_ohlcv(self, symbol, *, interval, count):
            self.calls.append((symbol, interval, count))
            return next(data for data in sources().values() if data["symbol"] == symbol)
    client = Client()
    payload = asyncio.run(scan.collect(client, now=NOW))
    assert payload["current"]["imbalance"] == 60
    assert client.calls == [(symbol, "1D", 5000) for symbol in scan.SYMBOLS.values()]


def test_auth_failure_propagates_without_fabrication():
    class Client:
        async def get_ohlcv(self, *args, **kwargs):
            raise RuntimeError("TV_NOT_CONFIGURED")
    with pytest.raises(RuntimeError, match="TV_NOT_CONFIGURED"):
        asyncio.run(scan.collect(Client(), now=NOW))


def test_persistence_only_complete_rows_and_atomic_snapshot(monkeypatch, tmp_path):
    payload = scan.build_output(sources(), now=NOW)
    payload["series"][0]["bid"] = None
    payload["series"][0]["imbalance"] = None
    mock = Mock()
    monkeypatch.setattr(scan, "writer", mock)
    monkeypatch.setattr(scan, "CACHE_PATH", tmp_path / "imbalance.json")
    scan.persist_result(payload)
    assert mock.upsert_imbalance_rows.call_args.args[0] == [payload["series"][1]]
    mock.upsert_scan_snapshot.assert_called_once_with(scan.SERVICE, payload["scan_time"], payload)


def test_no_join_retains_prior_snapshot(monkeypatch):
    mock = Mock()
    monkeypatch.setattr(scan, "writer", mock)
    with pytest.raises(RuntimeError, match="no aligned"):
        scan.persist_result({"missing": True})
    assert not mock.mock_calls


def test_writer_idempotent_revisions_and_bounded_chunks(monkeypatch):
    from db import writer
    db = sqlite3.connect(":memory:")
    db.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT)")
    migration = Path(__file__).parents[1] / "db/migrations/0108_imbalance.sql"
    db.executescript(migration.read_text())
    monkeypatch.setattr(writer, "get_db", lambda: db)
    monkeypatch.setattr(writer, "_PRICE_HISTORY_INSERT_CHUNK_ROWS", 1)
    rows = scan.build_output(sources(), now=NOW)["series"]
    writer.upsert_imbalance_rows(rows, recorded_at=NOW.isoformat())
    rows[0]["ask"] = 200
    rows[0]["imbalance"] = 160
    writer.upsert_imbalance_rows(rows, recorded_at=NOW.isoformat())
    assert db.execute("SELECT count(*) FROM imbalance_history").fetchone()[0] == 2
    assert db.execute("SELECT ask FROM imbalance_history ORDER BY date LIMIT 1").fetchone()[0] == 200


def test_daily_unit_and_manifest_registration():
    root = Path(__file__).parents[2]
    service = (root / "cloud/services/radon-imbalance.service").read_text()
    timer = (root / "cloud/services/radon-imbalance.timer").read_text()
    assert "TimeoutStartSec=240" in service
    assert scan.COLLECTION_TIMEOUT < 240
    assert "OnCalendar=*-*-* 18:25:00 America/New_York" in timer
    for path in ("cloud/config/auto-sync-units.txt", "cloud/config/installed-units.sha256", "cloud/scripts/setup-vps.sh"):
        text = (root / path).read_text()
        assert "radon-imbalance.service" in text
        assert "radon-imbalance.timer" in text


def test_run_heartbeats_unchanged_and_errors_on_stale_snapshot(monkeypatch):
    from contextlib import contextmanager
    payload = scan.build_output(sources(), now=NOW)
    cycles = []
    @contextmanager
    def cycle(service, *, market_hours_class):
        assert service == scan.SERVICE and market_hours_class == "daily"
        handle = Mock(finished_at=None)
        cycles.append(handle)
        yield handle
    async def collect(client):
        return payload
    persist = Mock()
    monkeypatch.setattr(scan, "service_cycle", cycle)
    monkeypatch.setattr(scan, "collect", collect)
    monkeypatch.setattr(scan, "persist_result", persist)
    assert scan.run(client=object()) == payload
    assert scan.run(client=object()) == payload
    assert persist.call_count == 2
    payload["degraded"] = True
    with pytest.raises(RuntimeError, match="latest expected"):
        scan.run(client=object())
    assert persist.call_count == 3


def test_cli_json_and_failure_stderr(monkeypatch, capsys):
    payload = scan.build_output(sources(), now=NOW)
    monkeypatch.setattr(scan, "run", lambda: payload)
    assert scan.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data_date"] == "2026-11-06"
    def failure():
        raise RuntimeError("TV_NOT_CONFIGURED")
    monkeypatch.setattr(scan, "run", failure)
    assert scan.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "TV_NOT_CONFIGURED" in captured.err
