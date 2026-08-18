"""Tests for the F3 catalyst feed aggregator (scripts/fetch_catalysts.py).

The aggregator fans out across four UW endpoints (premarket + afterhours
earnings, FDA calendar, economic calendar) and normalises every event into a
single catalyst-row shape:

    {ticker, type, title, date, source, days_until}

Tests inject a fixed "now" so days_until is deterministic and mock every UW
method so nothing touches the network. The Turso cache write is patched and
asserted-called rather than executed (the DB client refuses real connections
under pytest by design).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import fetch_catalysts as fc


# A fixed reference "now" — 2026-06-21 (Sunday). days_until is measured in
# whole calendar days from this date (ET).
FIXED_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def _make_client() -> MagicMock:
    client = MagicMock()
    client.get_earnings_premarket.return_value = {
        "data": [
            {"symbol": "AAPL", "full_name": "APPLE INC", "report_date": "2026-06-23",
             "report_time": "premarket", "expected_move_perc": "0.045"},
        ]
    }
    client.get_earnings_afterhours.return_value = {
        "data": [
            {"symbol": "NVDA", "full_name": "NVIDIA CORP", "report_date": "2026-06-25",
             "report_time": "postmarket", "expected_move_perc": "0.060"},
        ]
    }
    client.get_fda_calendar.return_value = {
        "data": [
            {"ticker": "IOVA", "drug": "Lifileucel", "catalyst": "PDUFA Date",
             "start_date": "2025-06-21", "end_date": None,
             "target_date": "2026-06-21",
             "indication": "Advanced Melanoma"},
        ]
    }
    client.get_economic_calendar.return_value = {
        "data": [
            {"event": "PCE index", "time": "2026-06-26T13:30:00Z",
             "type": "report", "forecast": "0.2%", "prev": "0.1%",
             "reported_period": "May"},
        ]
    }
    return client


def test_normalize_produces_catalyst_row_shape():
    client = _make_client()
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)

    assert rows, "expected normalised catalyst rows"
    required = {"ticker", "type", "title", "date", "source", "days_until"}
    for row in rows:
        assert required.issubset(row.keys()), f"missing keys in {row}"


def test_each_source_is_represented():
    client = _make_client()
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    types = {r["type"] for r in rows}
    assert "earnings" in types
    assert "fda" in types
    assert "economic" in types


def test_earnings_rows_normalized():
    client = _make_client()
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    aapl = next(r for r in rows if r["ticker"] == "AAPL")
    assert aapl["type"] == "earnings"
    assert aapl["date"] == "2026-06-23"
    assert aapl["source"] == "earnings_premarket"
    # 2026-06-21 -> 2026-06-23 is two days out.
    assert aapl["days_until"] == 2

    nvda = next(r for r in rows if r["ticker"] == "NVDA")
    assert nvda["source"] == "earnings_afterhours"
    assert nvda["days_until"] == 4


def test_fda_row_normalized_with_drug_in_title():
    client = _make_client()
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    iova = next(r for r in rows if r["ticker"] == "IOVA")
    assert iova["type"] == "fda"
    assert iova["date"] == "2026-06-21"
    assert iova["days_until"] == 0  # today
    assert "Lifileucel" in iova["title"]


def test_economic_row_has_no_ticker_and_date_from_time():
    client = _make_client()
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    econ = next(r for r in rows if r["type"] == "economic")
    assert econ["ticker"] is None
    assert econ["date"] == "2026-06-26"
    assert econ["event_time"] == "2026-06-26T13:30:00Z"
    assert econ["days_until"] == 5
    assert econ["title"] == "PCE index"


def test_rows_sorted_by_days_until_ascending():
    client = _make_client()
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    days = [r["days_until"] for r in rows]
    assert days == sorted(days)


def test_earnings_queries_next_five_trading_sessions():
    client = _make_client()
    client.get_earnings_premarket.return_value = {"data": []}
    client.get_earnings_afterhours.return_value = {"data": []}

    fc.fetch_catalysts(
        client=client,
        now=datetime(2026, 8, 7, 16, 0, tzinfo=timezone.utc),
    )

    expected = [
        "2026-08-07",
        "2026-08-10",
        "2026-08-11",
        "2026-08-12",
        "2026-08-13",
    ]
    for method in (client.get_earnings_premarket, client.get_earnings_afterhours):
        assert [call.kwargs["date"] for call in method.call_args_list] == expected
        assert all(call.kwargs["limit"] == 100 for call in method.call_args_list)
        assert all(call.kwargs["page"] == 0 for call in method.call_args_list)


def test_earnings_pagination_stops_after_short_page():
    client = _make_client()
    client.get_earnings_afterhours.return_value = {"data": []}

    def paged_premarket(*, date, limit, page):
        if date != "2026-08-07":
            return {"data": []}
        if page == 0:
            return {
                "data": [
                    {"symbol": f"T{i:03}", "full_name": f"Ticker {i}",
                     "report_date": date, "expected_move_perc": "0.01"}
                    for i in range(100)
                ]
            }
        if page == 1:
            return {
                "data": [
                    {"symbol": "LAST", "full_name": "Last ticker",
                     "report_date": date, "expected_move_perc": "0.01"}
                ]
            }
        raise AssertionError("pagination must stop after a short page")

    client.get_earnings_premarket.side_effect = paged_premarket
    rows = fc.fetch_catalysts(
        client=client,
        now=datetime(2026, 8, 7, 16, 0, tzinfo=timezone.utc),
    )

    assert any(row["ticker"] == "LAST" for row in rows)
    aug7_pages = [
        call.kwargs["page"]
        for call in client.get_earnings_premarket.call_args_list
        if call.kwargs["date"] == "2026-08-07"
    ]
    assert aug7_pages == [0, 1]


def test_past_events_excluded_by_default():
    client = _make_client()
    client.get_fda_calendar.return_value = {
        "data": [
            {"ticker": "OLD", "drug": "X", "catalyst": "PDUFA Date",
             "start_date": "2026-06-10", "end_date": "2026-06-10"},
        ]
    }
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    assert all(r["ticker"] != "OLD" for r in rows)


def test_partial_source_failure_is_tolerated():
    client = _make_client()
    client.get_economic_calendar.side_effect = RuntimeError("UW 500")
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    # Other sources still produce rows; economic is simply absent.
    assert rows
    assert all(r["type"] != "economic" for r in rows)


def test_run_writes_cache(monkeypatch, tmp_path):
    client = _make_client()
    writes = {}

    def _fake_upsert(rows, scan_time):
        writes["rows"] = rows
        writes["scan_time"] = scan_time

    monkeypatch.setattr(fc, "_write_db_cache", _fake_upsert)
    monkeypatch.setattr(fc, "CATALYSTS_JSON", tmp_path / "catalysts.json")

    result = fc.run(client=client, now=FIXED_NOW)

    assert "rows" in writes, "expected DB cache write to be called"
    assert writes["rows"] == result["catalysts"]
    assert (tmp_path / "catalysts.json").exists()


def test_economic_row_carries_forecast_prev_reported_period():
    client = _make_client()
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    econ = next(r for r in rows if r["type"] == "economic")
    assert econ["forecast"] == "0.2%"
    assert econ["prev"] == "0.1%"
    assert econ["reported_period"] == "May"
    assert econ["actual"] is None


def test_economic_row_persists_uw_actual_when_present():
    client = _make_client()
    client.get_economic_calendar.return_value = {
        "data": [
            {"event": "PCE index", "time": "2026-06-26T13:30:00Z",
             "type": "report", "forecast": "0.2%", "prev": "0.1%",
             "reported_period": "May", "actual": "0.3%"},
        ]
    }
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    econ = next(r for r in rows if r["type"] == "economic")
    assert econ["actual"] == "0.3%"
    assert econ["forecast"] == "0.2%"
    assert econ["prev"] == "0.1%"
    assert econ["reported_period"] == "May"


def test_earnings_row_persists_street_mean_est_and_actual_eps():
    client = _make_client()
    client.get_earnings_premarket.return_value = {
        "data": [
            {"symbol": "AAPL", "full_name": "APPLE INC", "report_date": "2026-06-23",
             "report_time": "premarket", "expected_move_perc": "0.045",
             "street_mean_est": 1.52, "actual_eps": 1.61},
        ]
    }
    rows = fc.fetch_catalysts(client=client, now=FIXED_NOW)
    aapl = next(r for r in rows if r["ticker"] == "AAPL")
    assert aapl["street_mean_est"] == 1.52
    assert aapl["actual_eps"] == 1.61
    assert aapl["expected_move_perc"] == "0.045"


def _econ_row(*, title: str, event_time: str, actual=None, **extra) -> dict:
    day = event_time[:10]
    row = {
        "ticker": None,
        "type": "economic",
        "title": title,
        "date": day,
        "source": "economic",
        "days_until": 0,
        "event_time": event_time,
        "forecast": "220K",
        "prev": "218K",
        "actual": actual,
        "reported_period": "Jun 13",
    }
    row.update(extra)
    return row


def test_apply_fred_actuals_fills_icsa_for_released_jobless_claims():
    released = _econ_row(
        title="Weekly Jobless Claims",
        event_time="2026-06-18T12:30:00Z",
    )
    future = _econ_row(
        title="Weekly Jobless Claims",
        event_time="2026-06-25T12:30:00Z",
    )
    calls: list[str] = []

    def fetch_obs(series_id: str):
        calls.append(series_id)
        return 235000

    out = fc.apply_fred_actuals([released, future], fetch_obs, FIXED_NOW)
    assert out[0]["actual"] == 235000
    assert out[1]["actual"] is None
    assert calls == ["ICSA"]


def test_employment_report_is_not_mapped_to_payems():
    released = _econ_row(
        title="U.S. employment report",
        event_time="2026-06-18T12:30:00Z",
        forecast="118000",
        prev="172000",
    )
    calls: list[str] = []

    def fetch_obs(series_id: str):
        calls.append(series_id)
        return 158858

    out = fc.apply_fred_actuals([released], fetch_obs, FIXED_NOW)
    assert out[0]["actual"] is None
    assert calls == []


def test_leading_indicators_is_not_mapped_to_usslind():
    released = _econ_row(
        title="Leading Indicators",
        event_time="2026-06-18T14:00:00Z",
        forecast="0%",
        prev="-0.2%",
    )
    calls: list[str] = []

    def fetch_obs(series_id: str):
        calls.append(series_id)
        return {"2020-02-01": 1.72}

    out = fc.apply_fred_actuals([released], fetch_obs, FIXED_NOW)
    assert out[0]["actual"] is None
    assert calls == []


def test_apply_fred_actuals_skips_when_value_matches_prev():
    released = _econ_row(
        title="Weekly Jobless Claims",
        event_time="2026-06-18T12:30:00Z",
        prev="209000",
    )

    def fetch_obs(series_id: str):
        return {"2026-06-13": 209000}

    out = fc.apply_fred_actuals([released], fetch_obs, FIXED_NOW)
    assert out[0]["actual"] is None


def test_apply_fred_actuals_skips_stale_observation():
    released = _econ_row(
        title="Weekly Jobless Claims",
        event_time="2026-06-18T12:30:00Z",
        prev="218000",
    )

    def fetch_obs(series_id: str):
        return {"2020-02-01": 235000}

    out = fc.apply_fred_actuals([released], fetch_obs, FIXED_NOW)
    assert out[0]["actual"] is None


def test_apply_fred_actuals_swallows_overlay_failure():
    released = _econ_row(
        title="Weekly Jobless Claims",
        event_time="2026-06-18T12:30:00Z",
    )
    other = _econ_row(
        title="PCE index",
        event_time="2026-06-18T12:30:00Z",
        forecast="0.2%",
        prev="0.1%",
        reported_period="May",
    )
    other_snapshot = dict(other)

    def boom(_series_id: str):
        raise RuntimeError("FRED 500")

    out = fc.apply_fred_actuals([released, other], boom, FIXED_NOW)
    assert out[0]["actual"] is None
    assert out[1] == other_snapshot
