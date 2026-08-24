"""REL-049 / R-095, R-097, R-098, R-099 (all P1) — the new oneshots report
green through a total source outage.

R-095: `scan_universe` funnels `UWRateLimitError`, any per-ticker exception
and a legitimate `None` (no LEAPs on this name) into ONE `failed_tickers`
list, and 9dab450e made the exit rule "any non-zero result count -> exit 0".
UW's daily cap tripping after the third ticker yields results=2/failed=516;
`data/leap.json` is overwritten with 2 rows, `mirror_scan_snapshot` writes
`leap-scan = ok`, the oneshot exits 0, and the LEAP tab silently shows 2
names behind a green banner.

R-098: trin / credit-spread / iei-hyg heartbeat `ok` through a dead live
source. The degradation lives only in `payload["source"]`, which nothing
consumes. IB drops its NYSE index entitlement at 10:00 ET and `trin`
heartbeats ok every 5 minutes all day off a 30-minute-old print.
`fetch_ivrank.py` is the reference: `health_error` + `state="error"`.

R-099: `IB_MARKET_DATA_TYPES = (1, 3, 4)` and `_snapshot_ticker` returns the
FIRST ticker that yields any priceable value — including type 3 (delayed)
and type 4 (delayed-frozen). `_build_sample` hardcodes `source: "ib"` and
stamps `ts` from `datetime.now()`, so a 15-minute-delayed print is bucketed
into the CURRENT hour, feeds MA(10) and reaches the UI as a live IN ZONE
badge. The `NOT NULL source` column exists precisely to carry this.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))


class TestLeapExhaustion:
    def test_a_provider_wipeout_is_not_a_successful_scan(self, monkeypatch, tmp_path):
        """1 of 100 names succeeded because UW's cap tripped: that is an
        outage, not a 1-row LEAP tab behind a green banner."""
        import leap_scanner_uw as leap

        assert hasattr(leap, "MAX_PROVIDER_FAILURE_RATIO")
        assert leap.scan_is_exhausted(results=1, provider_failures=99) is True
        assert leap.scan_is_exhausted(results=99, provider_failures=1) is False

    def test_no_leaps_is_not_a_provider_failure(self):
        """~170 of 518 largecaps names legitimately have no LEAPs — the
        regression 9dab450e fixed. Those must not count toward the ratio."""
        import leap_scanner_uw as leap

        assert leap.scan_is_exhausted(results=348, provider_failures=0) is False

    def test_scan_universe_separates_provider_failures_from_empty_results(self):
        import inspect

        import leap_scanner_uw as leap

        source = inspect.getsource(leap.scan_universe)
        assert "provider_failures" in source, (
            "UWRateLimitError, arbitrary exceptions and a legitimate None all "
            "still land in one bucket"
        )


class TestTrinHonestDegradation:
    def _reload(self, monkeypatch, tmp_path):
        import fetch_trin

        monkeypatch.setattr(fetch_trin, "TRIN_JSON", tmp_path / "trin.json")
        return fetch_trin

    def test_a_dead_live_source_records_error_not_ok(self, monkeypatch, tmp_path):
        trin = self._reload(monkeypatch, tmp_path)
        recorded: list[tuple] = []

        monkeypatch.setattr(trin.writer, "ensure_no_replica_for_writers", lambda: None)
        monkeypatch.setattr(trin.writer, "upsert_trin_samples", lambda *a, **k: None)
        monkeypatch.setattr(trin.writer, "upsert_trin_daily_rows", lambda *a, **k: None)
        monkeypatch.setattr(trin.writer, "upsert_scan_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(
            trin.writer,
            "record_service_health",
            lambda service, state, **kw: recorded.append((service, state, kw.get("error"))),
        )
        monkeypatch.setattr(trin, "load_cached_samples", lambda: [
            {"ts": "2026-08-21T14:30:00Z", "session_date": "2026-08-21", "trin": 0.8,
             "adv": 1, "dec": 1, "up_vol": 1.0, "down_vol": 1.0, "source": "ib"}
        ])
        monkeypatch.setattr(trin, "load_cached_daily", lambda: [("2026-08-21", 0.9)])
        monkeypatch.setattr(trin, "sample_live", lambda: (None, "ib-failed"))
        monkeypatch.setattr(trin, "fetch_daily", lambda: [])

        trin.run()

        assert recorded, "no heartbeat at all"
        service, state, error = recorded[-1]
        assert service == "trin"
        assert state == "error", "a dead IB feed heartbeated ok off a cached print"
        assert "ib-failed" in str(error)

    def test_a_healthy_sample_still_records_ok(self, monkeypatch, tmp_path):
        trin = self._reload(monkeypatch, tmp_path)
        recorded: list[tuple] = []
        monkeypatch.setattr(trin.writer, "ensure_no_replica_for_writers", lambda: None)
        monkeypatch.setattr(trin.writer, "upsert_trin_samples", lambda *a, **k: None)
        monkeypatch.setattr(trin.writer, "upsert_trin_daily_rows", lambda *a, **k: None)
        monkeypatch.setattr(trin.writer, "upsert_scan_snapshot", lambda *a, **k: None)
        monkeypatch.setattr(
            trin.writer,
            "record_service_health",
            lambda service, state, **kw: recorded.append((service, state, kw.get("error"))),
        )
        live = {
            "ts": "2026-08-21T15:30:00Z", "session_date": "2026-08-21", "trin": 0.7,
            "adv": 1, "dec": 1, "up_vol": 1.0, "down_vol": 1.0, "source": "ib",
        }
        monkeypatch.setattr(trin, "load_cached_samples", lambda: [])
        monkeypatch.setattr(trin, "load_cached_daily", lambda: [])
        monkeypatch.setattr(trin, "sample_live", lambda: (live, "ib"))
        monkeypatch.setattr(trin, "fetch_daily", lambda: [("2026-08-21", 0.9)])

        trin.run()

        assert recorded[-1][1] == "ok"


class TestTrinRecordsTheMarketDataType:
    def test_a_delayed_print_is_labelled_and_refused_in_zone(self):
        import fetch_trin as trin

        sample = trin._build_sample(
            0.42, None, None, datetime(2026, 8, 21, 15, 30, tzinfo=timezone.utc),
            market_data_type=3,
        )
        assert sample["source"] == "ib-delayed"
        assert trin.classify_state(0.42, source=sample["source"]) != "in_zone"

    def test_a_live_print_keeps_its_zone(self):
        import fetch_trin as trin

        sample = trin._build_sample(
            0.42, None, None, datetime(2026, 8, 21, 15, 30, tzinfo=timezone.utc),
            market_data_type=1,
        )
        assert sample["source"] == "ib"
        assert trin.classify_state(0.42, source=sample["source"]) == "in_zone"

    def test_snapshot_reports_which_feed_answered(self):
        import inspect

        import fetch_trin as trin

        assert "market_data_type" in inspect.signature(trin._build_sample).parameters
        source = inspect.getsource(trin._snapshot_ticker)
        assert "data_type" in source and "return ticker, data_type" in source


# R-097 (the account-data purge) is drilled behaviourally against the real
# module in scripts/lib/demoMirrorReliability.test.js — a source grep cannot
# tell "inside the retry ladder" from "next to it".
