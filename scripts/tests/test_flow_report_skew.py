"""The single-ticker flow report carries the ticker's current 25-delta skew.

The value and its direction come from the same UW risk-reversal history the
Vol/Skew MR scanner reads (`vol_skew_mr_scanner.fetch_skew_snapshot`), so the
flow page and the scanner never disagree on a name's skew.
"""
from __future__ import annotations

from unittest.mock import patch

import flow_report
import vol_skew_mr_scanner as vsmr

_FAKE_FLOW = {
    "dark_pool": {"aggregate": {}, "daily": []},
    "options_flow": {"bias": "NO_DATA"},
    "combined_signal": "NO_SIGNAL",
    "market_status": "closed",
    "trading_day_progress": 1.0,
    "trading_days_checked": [],
}


def test_build_report_carries_the_skew_snapshot() -> None:
    snapshot = {
        "expiry": "2026-10-16",
        "delta": 25,
        "sessions": [{"date": "2026-09-16", "value": 3.1}, {"date": "2026-09-17", "value": 3.42}],
        "value": 3.42,
        "prior": 3.1,
        "change": 0.32,
        "path": "rising",
        "errors": [],
    }
    with patch.object(flow_report, "fetch_flow", return_value=_FAKE_FLOW), \
         patch.object(flow_report, "analyze_signal", return_value={"direction": "NEUTRAL", "strength": 0}), \
         patch.object(flow_report, "fetch_ticker_skew", return_value=snapshot) as mock_skew:
        report = flow_report.build_report("META")

    mock_skew.assert_called_once_with("META")
    assert report["skew"] == snapshot


def test_skew_failure_never_fails_the_report() -> None:
    class _BrokenClient:
        def __init__(self, **_kwargs):
            raise RuntimeError("UW token missing")

    with patch.object(flow_report, "UWClient", _BrokenClient):
        skew = flow_report.fetch_ticker_skew("META")

    assert skew["value"] is None
    assert skew["path"] == "unknown"
    assert skew["sessions"] == []
    assert any("UW token missing" in error for error in skew["errors"])


def test_skew_uses_the_scanner_snapshot_with_its_own_client() -> None:
    class _Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    seen = {}

    def _snapshot(client, ticker):
        seen["client"] = client
        seen["ticker"] = ticker
        return vsmr.unavailable_skew(["skew_history:no_future_listed_expiry"])

    with patch.object(flow_report, "UWClient", _Client), \
         patch.object(flow_report, "fetch_skew_snapshot", _snapshot):
        skew = flow_report.fetch_ticker_skew("meta")

    assert seen["ticker"] == "META"
    assert seen["client"].kwargs == {"max_retries": 0, "backoff_factor": 0}
    assert skew["path"] == "unknown"
    assert skew["errors"] == ["skew_history:no_future_listed_expiry"]
