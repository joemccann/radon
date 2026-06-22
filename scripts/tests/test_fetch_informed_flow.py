"""Tests for the F4 informed-flow surface (scripts/fetch_informed_flow.py).

The aggregator fans out across three Unusual Whales surfaces for a single
ticker — congress recent trades, insider transactions, and institutional
ownership — and normalises them into one payload:

    {ticker, congress_trades[], insider_trades[], institutional_summary}

Each congress / insider row is normalised to a stable shape so the web panel
and the scanner badge read one schema rather than UW's three. Tests mock every
UW method so nothing touches the network, inject a fixed "now" so recency math
is deterministic, and patch the Turso cache write (the DB client refuses real
connections under pytest by design).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import fetch_informed_flow as fif


FIXED_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


def _make_client() -> MagicMock:
    client = MagicMock()
    client.get_congress_recent_trades.return_value = {
        "data": [
            {
                "ticker": "AAPL",
                "reporter": "Nancy Pelosi",
                "transaction_date": "2026-06-18",
                "txn_type": "Purchase",
                "amounts": "$1,000,001 - $5,000,000",
            },
            {
                "ticker": "AAPL",
                "reporter": "Dan Crenshaw",
                "transaction_date": "2026-06-10",
                "txn_type": "Sale",
                "amounts": "$15,001 - $50,000",
            },
        ]
    }
    client.get_insider_transactions.return_value = {
        "data": [
            {
                "ticker": "AAPL",
                "full_name": "Timothy Cook",
                "officer_title": "CEO",
                "transaction_date": "2026-06-19",
                "transaction_code": "P",
                "shares": "10000",
                "price": "192.50",
            },
            {
                "ticker": "AAPL",
                "full_name": "Luca Maestri",
                "officer_title": "CFO",
                "transaction_date": "2026-06-05",
                "transaction_code": "S",
                "shares": "5000",
                "price": "190.00",
            },
        ]
    }
    client.get_institutional_ownership.return_value = {
        "data": {
            "ticker": "AAPL",
            "institutional_ownership": "0.62",
            "total_institutions": 4821,
            "shares_outstanding": "15000000000",
            "report_date": "2026-03-31",
        }
    }
    return client


def test_payload_has_required_top_level_shape():
    client = _make_client()
    payload = fif.fetch_informed_flow("aapl", client=client, now=FIXED_NOW)
    required = {"ticker", "congress_trades", "insider_trades", "institutional_summary"}
    assert required.issubset(payload.keys())
    assert payload["ticker"] == "AAPL"
    assert isinstance(payload["congress_trades"], list)
    assert isinstance(payload["insider_trades"], list)


def test_congress_rows_normalized():
    client = _make_client()
    payload = fif.fetch_informed_flow("AAPL", client=client, now=FIXED_NOW)
    rows = payload["congress_trades"]
    assert rows, "expected congress rows"
    required = {"ticker", "person", "date", "side", "amount", "days_ago"}
    for row in rows:
        assert required.issubset(row.keys()), f"missing keys in {row}"
    pelosi = next(r for r in rows if r["person"] == "Nancy Pelosi")
    assert pelosi["side"] == "BUY"
    assert pelosi["date"] == "2026-06-18"
    assert pelosi["days_ago"] == 3


def test_insider_rows_normalized_with_buy_sell_side():
    client = _make_client()
    payload = fif.fetch_informed_flow("AAPL", client=client, now=FIXED_NOW)
    rows = payload["insider_trades"]
    assert rows
    required = {"ticker", "person", "title", "date", "side", "shares", "days_ago"}
    for row in rows:
        assert required.issubset(row.keys()), f"missing keys in {row}"
    cook = next(r for r in rows if r["person"] == "Timothy Cook")
    assert cook["side"] == "BUY"  # transaction_code P
    maestri = next(r for r in rows if r["person"] == "Luca Maestri")
    assert maestri["side"] == "SELL"  # transaction_code S


def test_institutional_summary_normalized():
    client = _make_client()
    payload = fif.fetch_informed_flow("AAPL", client=client, now=FIXED_NOW)
    summary = payload["institutional_summary"]
    assert summary is not None
    assert summary["ownership_pct"] == pytest.approx(62.0)
    assert summary["total_institutions"] == 4821


def test_rows_sorted_recent_first():
    client = _make_client()
    payload = fif.fetch_informed_flow("AAPL", client=client, now=FIXED_NOW)
    congress_days = [r["days_ago"] for r in payload["congress_trades"]]
    insider_days = [r["days_ago"] for r in payload["insider_trades"]]
    assert congress_days == sorted(congress_days)
    assert insider_days == sorted(insider_days)


def test_partial_source_failure_is_tolerated():
    client = _make_client()
    client.get_institutional_ownership.side_effect = RuntimeError("UW 500")
    payload = fif.fetch_informed_flow("AAPL", client=client, now=FIXED_NOW)
    # Congress + insider still populate; institutional is simply absent.
    assert payload["congress_trades"]
    assert payload["insider_trades"]
    assert payload["institutional_summary"] is None


def test_empty_results_produce_empty_lists_not_error():
    client = MagicMock()
    client.get_congress_recent_trades.return_value = {"data": []}
    client.get_insider_transactions.return_value = {"data": []}
    client.get_institutional_ownership.return_value = {"data": {}}
    payload = fif.fetch_informed_flow("ZZZZ", client=client, now=FIXED_NOW)
    assert payload["congress_trades"] == []
    assert payload["insider_trades"] == []
    assert payload["institutional_summary"] is None


def test_has_activity_helper():
    client = _make_client()
    payload = fif.fetch_informed_flow("AAPL", client=client, now=FIXED_NOW)
    assert fif.has_informed_activity(payload) is True

    empty = {"ticker": "ZZZZ", "congress_trades": [], "insider_trades": [], "institutional_summary": None}
    assert fif.has_informed_activity(empty) is False


def test_run_caches_only_when_non_empty(monkeypatch, tmp_path):
    """Structurally empty payloads must NOT be cached as a success (cf.
    feedback_dont_cache_empty_results)."""
    client = _make_client()
    writes = {}

    def _fake_db(payload, scan_time):
        writes["payload"] = payload
        writes["scan_time"] = scan_time

    monkeypatch.setattr(fif, "_write_db_cache", _fake_db)
    monkeypatch.setattr(fif, "DATA_DIR", tmp_path)

    result = fif.run("AAPL", client=client, now=FIXED_NOW)

    assert "payload" in writes, "expected DB cache write for a non-empty payload"
    assert (tmp_path / "informed_flow" / "AAPL.json").exists()
    assert result["ticker"] == "AAPL"


def test_run_skips_cache_for_empty_payload(monkeypatch, tmp_path):
    client = MagicMock()
    client.get_congress_recent_trades.return_value = {"data": []}
    client.get_insider_transactions.return_value = {"data": []}
    client.get_institutional_ownership.return_value = {"data": {}}
    writes = {}

    def _fake_db(payload, scan_time):
        writes["payload"] = payload

    monkeypatch.setattr(fif, "_write_db_cache", _fake_db)
    monkeypatch.setattr(fif, "DATA_DIR", tmp_path)

    result = fif.run("ZZZZ", client=client, now=FIXED_NOW)

    assert "payload" not in writes, "must not cache a structurally empty payload as success"
    assert not (tmp_path / "informed_flow" / "ZZZZ.json").exists()
    # The payload is still returned so the caller can render an empty state.
    assert result["ticker"] == "ZZZZ"
    assert result["congress_trades"] == []
