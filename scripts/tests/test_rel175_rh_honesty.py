"""REL-175 (R-483, R-486): the Robinhood quote and the crowding job fail
honestly."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import fetch_rh_crowding as crowding  # noqa: E402
from clients import robinhood_client as rc  # noqa: E402


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture()
def quote_rows(monkeypatch):
    holder: dict = {"rows": []}

    class FakeClient:
        def get_equity_quotes(self, symbols):
            return holder["rows"]

        def get_index_quotes(self, symbols):
            return holder["rows"]

    monkeypatch.setattr(rc, "robinhood_configured", lambda: True)
    monkeypatch.setattr(rc, "_client", lambda: FakeClient())
    return holder


class TestQuoteHonesty:
    def test_mark_price_only_is_rejected(self, quote_rows):
        """R-486: a mark_price silently became SPY's stored session close."""
        quote_rows["rows"] = [{"mark_price": "641.10"}]
        assert rc.fetch_robinhood_quote("SPY") is None

    def test_a_two_day_old_last_is_rejected(self, quote_rows):
        stale = _iso(datetime.now(timezone.utc) - timedelta(days=2))
        quote_rows["rows"] = [{"last_trade_price": "641.10", "updated_at": stale}]
        assert rc.fetch_robinhood_quote("SPY") is None

    def test_a_fresh_last_passes(self, quote_rows):
        fresh = _iso(datetime.now(timezone.utc) - timedelta(minutes=5))
        quote_rows["rows"] = [{"last_trade_price": "641.10", "updated_at": fresh}]
        assert rc.fetch_robinhood_quote("SPY") == pytest.approx(641.10)

    def test_a_last_with_no_timestamp_still_passes(self, quote_rows):
        """The quote shape is unpublished; an absent timestamp must not kill
        the rung, only a PRESENT stale one."""
        quote_rows["rows"] = [{"last_trade_price": "641.10"}]
        assert rc.fetch_robinhood_quote("SPY") == pytest.approx(641.10)


class TestCrowdingJobFailsCleanly:
    def test_auth_error_is_a_clean_skip(self, monkeypatch, capsys):
        """R-483: the fetch sat outside any try — invalid_grant tracebacked
        where the docs promise a clean skip."""
        monkeypatch.setattr(rc, "robinhood_configured", lambda: True)

        def boom():
            raise rc.RobinhoodAuthError("invalid_grant")

        monkeypatch.setattr(crowding, "fetch_crowding", boom)
        assert crowding.run() is None
        assert "invalid_grant" in capsys.readouterr().err

    def test_persist_oserror_is_a_clean_skip(self, monkeypatch, capsys):
        monkeypatch.setattr(rc, "robinhood_configured", lambda: True)
        monkeypatch.setattr(
            crowding, "fetch_crowding", lambda: ([{"symbol": "SPY"}], {})
        )
        monkeypatch.setattr(
            crowding, "build_rows",
            lambda popular, scans, day: [{"symbol": "SPY"}],
        )

        def boom(rows, scan_time):
            raise OSError("disk full")

        monkeypatch.setattr(crowding, "persist", boom)
        assert crowding.run() is None
        assert "disk full" in capsys.readouterr().err

    def test_main_exits_zero_on_a_failed_run(self, monkeypatch):
        monkeypatch.setattr(rc, "robinhood_configured", lambda: True)

        def boom():
            raise rc.RobinhoodAuthError("invalid_grant")

        monkeypatch.setattr(crowding, "fetch_crowding", boom)
        monkeypatch.setattr(sys, "argv", ["fetch_rh_crowding.py"])
        assert crowding.main() is None  # returns, no raise


class TestCriRecordsQuoteSource:
    def test_post_close_snapshot_sources_are_recorded(self, monkeypatch):
        import cri_scan

        monkeypatch.setattr(
            cri_scan, "_fetch_ib_current_quote", lambda t: 100.0 if t == "SPY" else None
        )
        monkeypatch.setattr(
            cri_scan, "_fetch_rh_current_quote", lambda t: 20.0 if t == "VIX" else None
        )
        monkeypatch.setattr(
            cri_scan, "_fetch_yahoo_current_quote", lambda t: 105.0
        )
        monkeypatch.setattr(cri_scan, "fetch_cor1m_current_quote", lambda: 15.0)
        cri_scan.build_post_close_snapshot("2026-09-03", use_official_cboe_close=False)
        sources = cri_scan.last_post_close_snapshot_sources()
        assert sources.get("SPY") == "ib"
        assert sources.get("VIX") == "robinhood"
        assert sources.get("VVIX") == "yahoo"
