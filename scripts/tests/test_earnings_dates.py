"""Tests for the standalone earnings-date service (scripts/earnings_dates.py).

Answers: for ticker T and optional structure DTE D, is there an upcoming
earnings release within the window, and if so what date/session?

Uses UWClient.get_earnings_by_ticker. Tests mock the client so nothing hits
the network, and inject a fixed ``now`` so days_until is deterministic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest

import earnings_dates as ed


# ET morning on earnings day for HONA-like same-day AMC case.
FIXED_NOW_ET = datetime(2026, 8, 5, 10, 0, tzinfo=ZoneInfo("America/New_York"))
FIXED_NOW = FIXED_NOW_ET.astimezone(timezone.utc)

# Just before midnight ET → still 2026-08-04 in ET even if UTC has rolled.
ET_BOUNDARY_NOW = datetime(2026, 8, 5, 3, 30, tzinfo=timezone.utc)  # 23:30 ET Aug 4


def _hona_payload() -> dict:
    """Upcoming rows first (UW live shape). HONA next is same-day postmarket."""
    return {
        "data": [
            {
                "report_date": "2026-08-05",
                "report_time": "postmarket",
                "source": "company",
                "expected_move_perc": 0.0876,
                "actual_eps": None,
                "street_mean_est": 0.12,
            },
            {
                "report_date": "2026-05-07",
                "report_time": "postmarket",
                "source": "company",
                "expected_move_perc": 0.09,
                "actual_eps": 0.11,
            },
        ]
    }


def _aapl_payload() -> dict:
    return {
        "data": [
            {
                "report_date": "2026-10-30",
                "report_time": "after market close",
                "source": "estimation",
                "expected_move_perc": 4.5,
                "actual_eps": None,
            },
            {
                "report_date": "2026-07-31",
                "report_time": "AMC",
                "source": "company",
                "expected_move_perc": "0.05",
                "actual_eps": 1.57,
            },
        ]
    }


def _make_client(payload: dict | None = None) -> MagicMock:
    client = MagicMock()
    client.get_earnings_by_ticker.return_value = payload if payload is not None else _hona_payload()
    return client


# ── within_dte ────────────────────────────────────────────────────


def test_within_dte_inclusive_bounds():
    assert ed.within_dte(0, 16) is True
    assert ed.within_dte(16, 16) is True
    assert ed.within_dte(8, 16) is True
    assert ed.within_dte(17, 16) is False
    assert ed.within_dte(-1, 16) is False


# ── next_earnings ─────────────────────────────────────────────────


def test_hona_same_day_amc_next_earnings():
    client = _make_client(_hona_payload())
    row = ed.next_earnings(client, "HONA", now=FIXED_NOW)
    assert row is not None
    assert row["report_date"] == "2026-08-05"
    assert row["report_time"] == "postmarket"
    assert row["source"] == "company"
    assert row["days_until"] == 0
    assert row["expected_move_pct"] == pytest.approx(8.76)


def test_past_actual_eps_same_day_skipped():
    """Same-day row with actual_eps set is treated as past; take next future."""
    client = _make_client(
        {
            "data": [
                {
                    "report_date": "2026-08-05",
                    "report_time": "premarket",
                    "source": "company",
                    "expected_move_perc": 0.05,
                    "actual_eps": 0.42,
                },
                {
                    "report_date": "2026-11-04",
                    "report_time": "BMO",
                    "source": "estimation",
                    "expected_move_perc": 0.06,
                    "actual_eps": None,
                },
            ]
        }
    )
    row = ed.next_earnings(client, "XYZ", now=FIXED_NOW)
    assert row is not None
    assert row["report_date"] == "2026-11-04"
    assert row["days_until"] == 91
    assert row["report_time"] == "premarket"


def test_no_data_returns_none():
    client = _make_client({"data": []})
    assert ed.next_earnings(client, "ZZZZ", now=FIXED_NOW) is None


def test_all_past_returns_none():
    client = _make_client(
        {
            "data": [
                {
                    "report_date": "2026-05-01",
                    "report_time": "postmarket",
                    "source": "company",
                    "actual_eps": 1.0,
                }
            ]
        }
    )
    assert ed.next_earnings(client, "OLD", now=FIXED_NOW) is None


def test_expected_move_fraction_vs_percent():
    # Fraction < 1 → * 100
    client = _make_client(
        {
            "data": [
                {
                    "report_date": "2026-08-10",
                    "report_time": "postmarket",
                    "source": "company",
                    "expected_move_perc": 0.087,
                    "actual_eps": None,
                }
            ]
        }
    )
    row = ed.next_earnings(client, "FRAC", now=FIXED_NOW)
    assert row["expected_move_pct"] == pytest.approx(8.7)

    # Already percent (>= 1)
    client.get_earnings_by_ticker.return_value = {
        "data": [
            {
                "report_date": "2026-08-10",
                "report_time": "postmarket",
                "source": "company",
                "expected_move_perc": 8.7,
                "actual_eps": None,
            }
        ]
    }
    row = ed.next_earnings(client, "PCT", now=FIXED_NOW)
    assert row["expected_move_pct"] == pytest.approx(8.7)


def test_report_time_normalization():
    cases = [
        ("premarket", "premarket"),
        ("postmarket", "postmarket"),
        ("BMO", "premarket"),
        ("AMC", "postmarket"),
        ("before market open", "premarket"),
        ("after market close", "postmarket"),
        ("", "unknown"),
        (None, "unknown"),
        ("during market", "unknown"),
    ]
    for raw, expected in cases:
        assert ed.normalize_report_time(raw) == expected, f"raw={raw!r}"


def test_et_day_boundary_with_fixed_now():
    """UTC already 2026-08-05 but ET still Aug 4 → days_until uses ET calendar."""
    client = _make_client(
        {
            "data": [
                {
                    "report_date": "2026-08-05",
                    "report_time": "postmarket",
                    "source": "company",
                    "expected_move_perc": 0.1,
                    "actual_eps": None,
                }
            ]
        }
    )
    row = ed.next_earnings(client, "HONA", now=ET_BOUNDARY_NOW)
    assert row is not None
    assert row["report_date"] == "2026-08-05"
    assert row["days_until"] == 1  # ET today is still Aug 4


# ── annotate / batch ──────────────────────────────────────────────


def test_annotate_hona_within_dte_16():
    client = _make_client(_hona_payload())
    payload = ed.annotate(client, "HONA", dte=16, now=FIXED_NOW)
    assert payload["ticker"] == "HONA"
    assert payload["report_date"] == "2026-08-05"
    assert payload["report_time"] == "postmarket"
    assert payload["days_until"] == 0
    assert payload["source"] == "company"
    assert payload["expected_move_pct"] == pytest.approx(8.76)
    assert payload["within_dte"] is True
    assert payload["dte"] == 16
    assert payload["missing"] is False


def test_annotate_within_dte_null_when_dte_none():
    client = _make_client(_hona_payload())
    payload = ed.annotate(client, "HONA", dte=None, now=FIXED_NOW)
    assert payload["within_dte"] is None
    assert payload["dte"] is None
    assert payload["missing"] is False


def test_annotate_outside_dte():
    client = _make_client(_aapl_payload())
    payload = ed.annotate(client, "AAPL", dte=16, now=FIXED_NOW)
    assert payload["report_date"] == "2026-10-30"
    assert payload["days_until"] == 86  # Aug 5 → Oct 30
    assert payload["within_dte"] is False
    assert payload["source"] == "estimation"
    assert payload["report_time"] == "postmarket"
    assert payload["expected_move_pct"] == pytest.approx(4.5)
    assert payload["missing"] is False


def test_annotate_missing_when_no_upcoming():
    client = _make_client({"data": []})
    payload = ed.annotate(client, "ZZZZ", dte=16, now=FIXED_NOW)
    assert payload["ticker"] == "ZZZZ"
    assert payload["missing"] is True
    assert payload["report_date"] is None
    assert payload["report_time"] is None
    assert payload["days_until"] is None
    assert payload["source"] is None
    assert payload["expected_move_pct"] is None
    assert payload["within_dte"] is None  # no days_until; still null-ish
    assert payload["dte"] == 16


def test_batch_annotate():
    client = MagicMock()

    def _by_ticker(ticker: str):
        t = ticker.upper()
        if t == "HONA":
            return _hona_payload()
        if t == "AAPL":
            return _aapl_payload()
        return {"data": []}

    client.get_earnings_by_ticker.side_effect = _by_ticker
    results = ed.batch_annotate(
        client,
        [{"ticker": "HONA", "dte": 16}, {"ticker": "AAPL", "dte": 16}, {"ticker": "ZZZZ"}],
        now=FIXED_NOW,
    )
    assert len(results) == 3
    by_ticker = {r["ticker"]: r for r in results}
    assert by_ticker["HONA"]["within_dte"] is True
    assert by_ticker["HONA"]["days_until"] == 0
    assert by_ticker["AAPL"]["within_dte"] is False
    assert by_ticker["ZZZZ"]["missing"] is True
    assert by_ticker["ZZZZ"]["dte"] is None


def test_disk_cache_writes_on_success(monkeypatch, tmp_path):
    client = _make_client(_hona_payload())
    monkeypatch.setattr(ed, "CACHE_DIR", tmp_path / "earnings_dates")
    payload = ed.annotate(client, "HONA", dte=16, now=FIXED_NOW, cache=True)
    path = tmp_path / "earnings_dates" / "HONA.json"
    assert path.exists()
    assert payload["missing"] is False


def test_disk_cache_skips_network_error(monkeypatch, tmp_path):
    """Do not cache missing-only failures from network errors."""
    client = MagicMock()
    client.get_earnings_by_ticker.side_effect = RuntimeError("UW 500")
    monkeypatch.setattr(ed, "CACHE_DIR", tmp_path / "earnings_dates")
    payload = ed.annotate(client, "HONA", dte=16, now=FIXED_NOW, cache=True)
    assert payload["missing"] is True
    assert not (tmp_path / "earnings_dates" / "HONA.json").exists()
