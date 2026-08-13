"""Tests for daily NAV history tracking and equity curve construction."""
import json
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

# Module under test
from portfolio_performance import (
    load_nav_history,
    build_nav_equity_curve,
    parse_option_id,
    NAV_HISTORY_PATH,
)
import ib_sync


class TestLoadNavHistory:
    """load_nav_history() reads JSONL and returns date→nav mapping."""

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        """No nav_history.jsonl → empty dict."""
        path = tmp_path / "nav_history.jsonl"
        result = load_nav_history(path)
        assert result == {}

    def test_returns_empty_dict_when_file_empty(self, tmp_path):
        """Empty file → empty dict."""
        path = tmp_path / "nav_history.jsonl"
        path.write_text("")
        result = load_nav_history(path)
        assert result == {}

    def test_parses_valid_jsonl_entries(self, tmp_path):
        """Valid JSONL entries are parsed to date→nav dict."""
        path = tmp_path / "nav_history.jsonl"
        path.write_text(
            '{"date":"2026-03-18","nav":1152902.61}\n'
            '{"date":"2026-03-19","nav":1154861.21}\n'
        )
        result = load_nav_history(path)
        assert result == {
            "2026-03-18": 1152902.61,
            "2026-03-19": 1154861.21,
        }

    def test_skips_malformed_lines(self, tmp_path):
        """Malformed lines are skipped without crashing."""
        path = tmp_path / "nav_history.jsonl"
        path.write_text(
            '{"date":"2026-03-18","nav":1000000}\n'
            'not valid json\n'
            '{"date":"2026-03-19","nav":1010000}\n'
        )
        result = load_nav_history(path)
        assert len(result) == 2

    def test_last_entry_wins_for_duplicate_dates(self, tmp_path):
        """If a date appears twice, the last value wins (updated sync)."""
        path = tmp_path / "nav_history.jsonl"
        path.write_text(
            '{"date":"2026-03-19","nav":1000000}\n'
            '{"date":"2026-03-19","nav":1154861.21}\n'
        )
        result = load_nav_history(path)
        assert result["2026-03-19"] == 1154861.21


class TestBuildNavEquityCurve:
    """build_nav_equity_curve() creates a DataFrame from NAV snapshots."""

    def test_returns_none_when_fewer_than_2_points(self):
        """Need ≥2 NAV snapshots to compute returns."""
        assert build_nav_equity_curve({"2026-03-19": 1000000}) is None
        assert build_nav_equity_curve({}) is None

    def test_returns_dataframe_with_equity_and_returns(self):
        """Valid NAV history produces DataFrame with equity, daily_return, drawdown."""
        nav = {
            "2026-03-17": 1000000.0,
            "2026-03-18": 1010000.0,
            "2026-03-19": 1005000.0,
        }
        curve = build_nav_equity_curve(nav)
        assert curve is not None
        assert len(curve) == 3
        assert list(curve.columns) >= ["equity", "daily_return", "drawdown"]
        # First day: return is null/NaN
        assert curve.iloc[0]["daily_return"] != curve.iloc[0]["daily_return"]  # NaN
        # Second day: +1%
        assert abs(curve.iloc[1]["daily_return"] - 0.01) < 1e-8
        # Third day: -0.495%
        assert curve.iloc[2]["daily_return"] < 0

    def test_drawdown_tracks_peak(self):
        """Drawdown computed from running peak."""
        nav = {
            "2026-01-02": 1000000.0,
            "2026-01-03": 1100000.0,  # new peak
            "2026-01-06": 990000.0,   # drawdown from 1.1M peak
        }
        curve = build_nav_equity_curve(nav)
        assert curve is not None
        assert curve.iloc[0]["drawdown"] == 0.0
        assert curve.iloc[1]["drawdown"] == 0.0
        assert curve.iloc[2]["drawdown"] == pytest.approx((990000 / 1100000) - 1, abs=1e-6)


class TestParseOptionId:
    """parse_option_id() extracts symbol, expiry, right, strike from OCC-style ID."""

    def test_parses_call_option(self):
        symbol, expiry, right, strike = parse_option_id("AAPL260321C00230000")
        assert symbol == "AAPL"
        assert expiry == "20260321"
        assert right == "C"
        assert strike == 230.0

    def test_parses_put_option(self):
        symbol, expiry, right, strike = parse_option_id("SPY260321P00570000")
        assert symbol == "SPY"
        assert expiry == "20260321"
        assert right == "P"
        assert strike == 570.0

    def test_parses_fractional_strike(self):
        symbol, expiry, right, strike = parse_option_id("BRZE260320C00022500")
        assert symbol == "BRZE"
        assert strike == 22.5

    def test_parses_low_strike(self):
        symbol, expiry, right, strike = parse_option_id("RR260321C00007000")
        assert symbol == "RR"
        assert strike == 7.0

    def test_raises_on_invalid_format(self):
        with pytest.raises(ValueError):
            parse_option_id("INVALID")


def test_concurrent_updates_preserve_existing_history(tmp_path, monkeypatch):
    path = tmp_path / "nav_history.jsonl"
    path.write_text('{"date":"2026-01-01","nav":100.0}\n')
    monkeypatch.setattr(ib_sync, "NAV_HISTORY_PATH", path)

    threads = [
        threading.Thread(target=ib_sync._append_nav_snapshot, args=(value,))
        for value in (200.0, 300.0)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert any(row["date"] == "2026-01-01" and row["nav"] == 100.0 for row in rows)
    assert len({row["date"] for row in rows}) == len(rows)


def test_interrupted_update_preserves_history(tmp_path, monkeypatch):
    path = tmp_path / "nav_history.jsonl"
    original = '{"date":"2026-01-01","nav":100.0}\n'
    path.write_text(original)
    monkeypatch.setattr(ib_sync, "NAV_HISTORY_PATH", path)
    monkeypatch.setattr(ib_sync.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(OSError, match="boom"):
        ib_sync._append_nav_snapshot(200.0)

    assert path.read_text() == original
