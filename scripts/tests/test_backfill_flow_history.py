"""Tests for the dark-pool-cache -> ticker_flow_history backfill (no torch, no DB).

The dark-pool cache CACHE_DIR is already pointed at a per-test tmp dir by the
autouse ``_isolate_darkpool_cache`` fixture in conftest. We drop real cache
fixture files there, stub ``fetch_flow.analyze_darkpool`` to a deterministic
aggregate, and record ``db.writer.upsert_ticker_flow_history`` calls instead of
hitting Turso.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import fetch_flow
from db import writer
from forecasting import backfill_flow_history
from utils import darkpool_cache


def _date(days_ago: int) -> str:
    return (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


def _write_cache_file(ticker: str, date: str, trades: list) -> None:
    cache_dir = darkpool_cache.CACHE_DIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ticker": ticker.upper(),
        "date": date,
        "count": len(trades),
        "schema": darkpool_cache.CACHE_SCHEMA,
        "cached_at": datetime.now().isoformat(),
        "trades": trades,
    }
    with open(cache_dir / f"{ticker.upper()}_{date}.json", "w") as f:
        json.dump(payload, f)


_FAKE_AGG = {
    "total_volume": 4242,
    "total_premium": 123456.0,
    "buy_volume": 3000,
    "sell_volume": 1242,
    "dp_buy_ratio": 0.71,
    "flow_direction": "ACCUMULATION",
    "flow_strength": 42.0,
    "num_prints": 999,  # deliberately != len(trades) to prove num_prints<-len(trades)
}


@pytest.fixture
def stub_analyze(monkeypatch):
    monkeypatch.setattr(
        fetch_flow, "analyze_darkpool", lambda trades: dict(_FAKE_AGG)
    )


@pytest.fixture
def recorder(monkeypatch):
    calls = []

    def _record(ticker, date, **kwargs):
        calls.append((ticker, date, kwargs))

    monkeypatch.setattr(writer, "upsert_ticker_flow_history", _record)
    return calls


def test_filename_parsing_multi_underscore_ticker():
    assert backfill_flow_history._parse_cache_filename(
        "BRK_B_2026-06-18.json"
    ) == ("BRK_B", "2026-06-18")
    assert backfill_flow_history._parse_cache_filename(
        "AAPL_2026-06-18.json"
    ) == ("AAPL", "2026-06-18")
    assert backfill_flow_history._parse_cache_filename("garbage.json") is None
    assert backfill_flow_history._parse_cache_filename("AAPL_bad.json") is None


def test_aggregate_maps_fields_and_num_prints_is_len(stub_analyze):
    trades = [{"size": 1}, {"size": 2}, {"size": 3}]
    _write_cache_file("AAPL", _date(2), trades)

    agg = backfill_flow_history.aggregate_cached_day("AAPL", _date(2))

    assert agg == {
        "flow_strength": 42.0,
        "buy_ratio": 0.71,
        "dp_direction": "ACCUMULATION",
        "total_premium": 123456.0,
        "total_volume": 4242,
        "num_prints": 3,  # len(trades), NOT agg["num_prints"]
    }


def test_aggregate_returns_none_when_no_cache(stub_analyze):
    assert backfill_flow_history.aggregate_cached_day("NOPE", _date(2)) is None


def test_backfill_writes_mapped_fields(stub_analyze, recorder):
    trades = [{"size": 1}, {"size": 2}]
    _write_cache_file("BRK_B", _date(3), trades)

    out = backfill_flow_history.backfill_from_cache(days=20)

    assert out["written"] == 1
    assert out["tickers_touched"] == ["BRK_B"]
    assert len(recorder) == 1
    ticker, date, kwargs = recorder[0]
    assert ticker == "BRK_B"
    assert date == _date(3)
    assert kwargs == {
        "flow_strength": 42.0,
        "buy_ratio": 0.71,
        "dp_direction": "ACCUMULATION",
        "total_premium": 123456.0,
        "total_volume": 4242,
        "num_prints": 2,
    }


def test_days_filter_excludes_old_dates(stub_analyze, recorder):
    _write_cache_file("AAPL", _date(2), [{"size": 1}])
    _write_cache_file("AAPL", _date(40), [{"size": 1}])

    out = backfill_flow_history.backfill_from_cache(days=20)

    # Both files scanned, but only the recent one is written.
    assert out["scanned_files"] == 2
    assert out["written"] == 1
    assert [c[1] for c in recorder] == [_date(2)]


def test_empty_cache_day_skipped(stub_analyze, recorder):
    # An empty-trades cache file: get_cached_darkpool returns [] -> skipped.
    _write_cache_file("MSFT", _date(2), [])
    _write_cache_file("AAPL", _date(2), [{"size": 1}])

    out = backfill_flow_history.backfill_from_cache(days=20)

    assert out["skipped_empty"] == 1
    assert out["written"] == 1
    assert out["tickers_touched"] == ["AAPL"]


def test_ticker_filter_uppercases(stub_analyze, recorder):
    _write_cache_file("AAPL", _date(2), [{"size": 1}])
    _write_cache_file("MSFT", _date(2), [{"size": 1}])

    out = backfill_flow_history.backfill_from_cache(tickers=["aapl"], days=20)

    assert out["written"] == 1
    assert out["tickers_touched"] == ["AAPL"]
    assert [c[0] for c in recorder] == ["AAPL"]


def test_per_pair_error_is_swallowed(stub_analyze, monkeypatch):
    _write_cache_file("AAA", _date(2), [{"size": 1}])
    _write_cache_file("BBB", _date(2), [{"size": 1}])

    calls = []

    def _flaky(ticker, date, **kwargs):
        if ticker == "AAA":
            raise RuntimeError("boom")
        calls.append((ticker, date, kwargs))

    monkeypatch.setattr(writer, "upsert_ticker_flow_history", _flaky)

    out = backfill_flow_history.backfill_from_cache(days=20)

    assert out["errors"] >= 1
    assert out["written"] == 1
    assert [c[0] for c in calls] == ["BBB"]
    assert out["tickers_touched"] == ["BBB"]


def test_summary_counts_complete(stub_analyze, recorder):
    _write_cache_file("AAPL", _date(2), [{"size": 1}])
    _write_cache_file("MSFT", _date(5), [{"size": 1}])
    _write_cache_file("OLD", _date(40), [{"size": 1}])
    _write_cache_file("EMPTY", _date(3), [])

    out = backfill_flow_history.backfill_from_cache(days=20)

    assert set(out.keys()) == {
        "scanned_files",
        "written",
        "skipped_empty",
        "errors",
        "tickers_touched",
    }
    assert out["scanned_files"] == 4
    assert out["written"] == 2
    assert out["skipped_empty"] == 1  # EMPTY (OLD is filtered before aggregate)
    assert out["errors"] == 0
    assert out["tickers_touched"] == ["AAPL", "MSFT"]
