"""Robinhood rank in every failover ladder: IB > UW > Cboe > Robinhood > Yahoo.

Per-ladder pins that the Robinhood rung sits BELOW IB / UW (and Cboe where a
Cboe rung exists) and ABOVE Yahoo, and that an unconfigured Robinhood skips
cleanly to Yahoo. The credit-spread and IEI/HYG cascades carry their own twin
tests next to their existing cascade suites.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import cri_scan  # noqa: E402
import garch_convergence  # noqa: E402
import leap_scanner_uw  # noqa: E402
import portfolio_risk  # noqa: E402
import rv_ratio_scan  # noqa: E402

BARS = {"2026-08-27": 100.0, "2026-08-28": 101.0}


class TestPortfolioRiskLadder:
    def _wire(self, monkeypatch, *, ib=None, uw=None, rh=None, yahoo=None, calls=None):
        calls = calls if calls is not None else []

        def rung(label, result):
            def fetch(symbol):
                calls.append(label)
                return result or {}
            return fetch

        monkeypatch.setattr(portfolio_risk, "_ib_reachable", lambda: True)
        monkeypatch.setattr(portfolio_risk, "_fetch_ib_closes", rung("ib", ib))
        monkeypatch.setattr(portfolio_risk, "_fetch_uw_closes", rung("uw", uw))
        monkeypatch.setattr(portfolio_risk, "_fetch_rh_closes", rung("rh", rh))
        monkeypatch.setattr(portfolio_risk, "_fetch_yahoo_closes", rung("yahoo", yahoo))
        return calls

    def test_rh_sits_between_uw_and_yahoo(self, monkeypatch):
        calls = self._wire(monkeypatch, rh=BARS)
        closes, source = portfolio_risk._fetch_closes_via_ladder("SPY")
        assert (closes, source) == (BARS, "rh")
        assert calls == ["ib", "uw", "rh"], "RH must run after IB/UW and preempt Yahoo"

    def test_rh_never_preempts_ib_or_uw(self, monkeypatch):
        calls = self._wire(monkeypatch, uw=BARS, rh=BARS)
        _, source = portfolio_risk._fetch_closes_via_ladder("SPY")
        assert source == "uw"
        assert "rh" not in calls

    def test_empty_rh_falls_through_to_yahoo(self, monkeypatch):
        calls = self._wire(monkeypatch, yahoo=BARS)
        closes, source = portfolio_risk._fetch_closes_via_ladder("SPY")
        assert (closes, source) == (BARS, "yahoo")
        assert calls == ["ib", "uw", "rh", "yahoo"]

    def test_deadline_between_uw_and_rh_aborts_without_calling_rh(self, monkeypatch):
        calls = self._wire(monkeypatch)
        ticks = iter([0.0, 10.0])  # after-IB check passes, after-UW check trips

        closes, source = portfolio_risk._fetch_closes_via_ladder(
            "SPY", deadline=5.0, clock=lambda: next(ticks)
        )
        assert source == portfolio_risk._LADDER_DEADLINE
        assert "rh" not in calls and "yahoo" not in calls

    def test_ladder_rung_count_matches_the_budget_math(self):
        assert portfolio_risk.BACKFILL_LADDER_RUNGS == 4

    def test_unconfigured_rh_rung_is_a_clean_networkless_empty(self, monkeypatch):
        monkeypatch.delenv("ROBINHOOD_MCP_TOKEN", raising=False)
        monkeypatch.setattr(
            "requests.Session.post",
            lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("unconfigured RH rung attempted network I/O")
            ),
        )
        assert portfolio_risk._fetch_rh_closes("SPY") == {}


class TestRvRatioIncrementalChain:
    def test_rh_fills_before_yahoo(self, monkeypatch):
        yahoo_calls: list = []
        monkeypatch.setattr(rv_ratio_scan, "_ib_auth_state", lambda: "awaiting_2fa")
        monkeypatch.setattr(rv_ratio_scan, "_fetch_uw_daily", lambda s: {})
        monkeypatch.setattr(rv_ratio_scan, "_fetch_rh_daily", lambda s: dict(BARS))
        monkeypatch.setattr(
            rv_ratio_scan, "_fetch_yahoo_daily",
            lambda s, years: yahoo_calls.append(s) or {},
        )

        bars, source = rv_ratio_scan._fetch_incremental("SPY")
        assert (bars, source) == (BARS, "rh")
        assert yahoo_calls == []

    def test_index_symbols_skip_rh_and_go_to_yahoo(self, monkeypatch):
        rh_calls: list = []
        monkeypatch.setattr(rv_ratio_scan, "_ib_auth_state", lambda: "awaiting_2fa")
        monkeypatch.setattr(
            rv_ratio_scan, "_fetch_rh_daily", lambda s: rh_calls.append(s) or {}
        )
        monkeypatch.setattr(
            rv_ratio_scan, "_fetch_yahoo_daily", lambda s, years: dict(BARS)
        )

        bars, source = rv_ratio_scan._fetch_incremental("SPX")
        assert source == "yahoo"
        assert rh_calls == [], "index symbols must not touch Robinhood"

    def test_uw_hit_never_reaches_rh(self, monkeypatch):
        rh_calls: list = []
        monkeypatch.setattr(rv_ratio_scan, "_ib_auth_state", lambda: "awaiting_2fa")
        monkeypatch.setattr(rv_ratio_scan, "_fetch_uw_daily", lambda s: dict(BARS))
        monkeypatch.setattr(
            rv_ratio_scan, "_fetch_rh_daily", lambda s: rh_calls.append(s) or {}
        )

        _, source = rv_ratio_scan._fetch_incremental("SPY")
        assert source == "uw"
        assert rh_calls == []

    def test_empty_rh_falls_through_to_yahoo(self, monkeypatch):
        yahoo_calls: list = []
        monkeypatch.setattr(rv_ratio_scan, "_ib_auth_state", lambda: "awaiting_2fa")
        monkeypatch.setattr(rv_ratio_scan, "_fetch_uw_daily", lambda s: {})
        monkeypatch.setattr(rv_ratio_scan, "_fetch_rh_daily", lambda s: {})
        monkeypatch.setattr(
            rv_ratio_scan, "_fetch_yahoo_daily",
            lambda s, years: yahoo_calls.append(s) or dict(BARS),
        )

        bars, source = rv_ratio_scan._fetch_incremental("SPY")
        assert (bars, source) == (BARS, "yahoo")
        assert yahoo_calls == ["SPY"], "an empty RH answer must still reach Yahoo"


class TestGarchAndLeapPriceHistory:
    def test_garch_uses_rh_before_yahoo(self, monkeypatch):
        yahoo_calls: list = []
        monkeypatch.setattr(garch_convergence, "_fetch_uw_prices", lambda *a, **k: [])
        monkeypatch.setattr(
            garch_convergence, "_fetch_rh_prices", lambda t: [100.0] * 90
        )
        monkeypatch.setattr(
            garch_convergence, "_fetch_yahoo_prices",
            lambda t, days=400: yahoo_calls.append(t) or [],
        )

        prices = garch_convergence._fetch_prices("SPY", uw_client=None)
        assert len(prices) == 90
        assert yahoo_calls == []

    def test_garch_short_rh_series_still_falls_to_yahoo(self, monkeypatch):
        monkeypatch.setattr(garch_convergence, "_fetch_uw_prices", lambda *a, **k: [])
        monkeypatch.setattr(garch_convergence, "_fetch_rh_prices", lambda t: [100.0] * 5)
        monkeypatch.setattr(
            garch_convergence, "_fetch_yahoo_prices", lambda t, days=400: [99.0] * 90
        )
        assert garch_convergence._fetch_prices("SPY", uw_client=None) == [99.0] * 90

    def test_leap_uses_rh_before_yahoo(self, monkeypatch):
        yahoo_calls: list = []
        monkeypatch.setattr(leap_scanner_uw, "get_uw_history", lambda *a, **k: [])
        monkeypatch.setattr(leap_scanner_uw, "get_rh_history", lambda t: [100.0] * 90)
        monkeypatch.setattr(
            leap_scanner_uw, "get_yahoo_history",
            lambda t, days=400: yahoo_calls.append(t) or [],
        )

        prices = leap_scanner_uw.get_price_history("SPY")
        assert len(prices) == 90
        assert yahoo_calls == []

    def test_leap_uw_hit_never_reaches_rh(self, monkeypatch):
        rh_calls: list = []
        monkeypatch.setattr(
            leap_scanner_uw, "get_uw_history", lambda *a, **k: [100.0] * 90
        )
        monkeypatch.setattr(
            leap_scanner_uw, "get_rh_history", lambda t: rh_calls.append(t) or []
        )
        leap_scanner_uw.get_price_history("SPY")
        assert rh_calls == []

    def test_leap_empty_rh_falls_through_to_yahoo(self, monkeypatch):
        yahoo_calls: list = []
        monkeypatch.setattr(leap_scanner_uw, "get_uw_history", lambda *a, **k: [])
        monkeypatch.setattr(leap_scanner_uw, "get_rh_history", lambda t: [])
        monkeypatch.setattr(
            leap_scanner_uw, "get_yahoo_history",
            lambda t, days=400: yahoo_calls.append(t) or [99.0] * 90,
        )

        assert leap_scanner_uw.get_price_history("SPY") == [99.0] * 90
        assert yahoo_calls == ["SPY"], "an empty RH answer must still reach Yahoo"


class TestCriScanRank:
    """CRI is the ladder where Cboe already outranks Yahoo — Robinhood slots
    AFTER Cboe and BEFORE Yahoo (IB > UW > Cboe > RH > Yahoo)."""

    def test_current_quote_tries_rh_after_ib_and_before_yahoo(self, monkeypatch):
        yahoo_calls: list = []
        monkeypatch.setattr(cri_scan, "_fetch_ib_current_quote", lambda t: None)
        monkeypatch.setattr(cri_scan, "_fetch_rh_current_quote", lambda t: 641.10)
        monkeypatch.setattr(
            cri_scan, "_fetch_yahoo_current_quote",
            lambda t: yahoo_calls.append(t) or 640.0,
        )

        assert cri_scan.fetch_preferred_current_quote("SPY") == pytest.approx(641.10)
        assert yahoo_calls == []

    def test_current_quote_prefers_ib_over_rh(self, monkeypatch):
        rh_calls: list = []
        monkeypatch.setattr(cri_scan, "_fetch_ib_current_quote", lambda t: 640.5)
        monkeypatch.setattr(
            cri_scan, "_fetch_rh_current_quote", lambda t: rh_calls.append(t) or 641.1
        )
        assert cri_scan.fetch_preferred_current_quote("SPY") == pytest.approx(640.5)
        assert rh_calls == []

    def test_current_quote_empty_rh_falls_through_to_yahoo(self, monkeypatch):
        yahoo_calls: list = []
        monkeypatch.setattr(cri_scan, "_fetch_ib_current_quote", lambda t: None)
        monkeypatch.setattr(cri_scan, "_fetch_rh_current_quote", lambda t: None)
        monkeypatch.setattr(
            cri_scan, "_fetch_yahoo_current_quote",
            lambda t: yahoo_calls.append(t) or 640.0,
        )

        assert cri_scan.fetch_preferred_current_quote("SPY") == pytest.approx(640.0)
        assert yahoo_calls == ["SPY"], "an empty RH answer must still reach Yahoo"

    def test_history_fallback_uses_rh_for_spy_before_yahoo(self, monkeypatch):
        bars = [
            (f"2026-{m:02d}-{d:02d}", 100.0) for m in range(1, 13) for d in range(1, 22)
        ]  # >= MIN_BARS unique sessions
        yahoo_calls: list = []
        monkeypatch.setattr(cri_scan, "_fetch_ib", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_uw", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_rh", lambda t: list(bars))
        monkeypatch.setattr(
            cri_scan, "_fetch_yahoo", lambda t, days=400: yahoo_calls.append(t) or []
        )

        raw, _ = cri_scan.fetch_all(["SPY"])
        assert "SPY" in raw
        assert yahoo_calls == [], "a Robinhood hit for SPY must not reach Yahoo"

    def test_history_fallback_never_asks_rh_for_index_tickers(self, monkeypatch):
        rh_calls: list = []
        bars = [(f"2026-{m:02d}-{d:02d}", 20.0) for m in range(1, 13) for d in range(1, 22)]
        monkeypatch.setattr(cri_scan, "_fetch_ib", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_uw", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_rh", lambda t: rh_calls.append(t) or [])
        monkeypatch.setattr(cri_scan, "_fetch_cboe_cor1m", lambda: [])
        monkeypatch.setattr(cri_scan, "_fetch_yahoo", lambda t, days=400: list(bars))
        monkeypatch.setattr(cri_scan.time, "sleep", lambda s: None)

        cri_scan.fetch_all(["VIX", "COR1M"])
        assert rh_calls == [], "VIX/VVIX/COR1M must not touch Robinhood"

    def test_cor1m_prefers_cboe_and_rh_is_never_in_that_lane(self, monkeypatch):
        """Cboe outranks RH: COR1M history goes IB -> Cboe -> Yahoo, and the
        RH rung is scoped to equities so it cannot shadow the official feed."""
        bars = [(f"2026-{m:02d}-{d:02d}", 20.0) for m in range(1, 13) for d in range(1, 22)]
        rh_calls: list = []
        yahoo_calls: list = []
        monkeypatch.setattr(cri_scan, "_fetch_ib", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_uw", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_cboe_cor1m", lambda: list(bars))
        monkeypatch.setattr(cri_scan, "_fetch_rh", lambda t: rh_calls.append(t) or [])
        monkeypatch.setattr(
            cri_scan, "_fetch_yahoo", lambda t, days=400: yahoo_calls.append(t) or []
        )

        raw, _ = cri_scan.fetch_all(["COR1M"])
        assert "COR1M" in raw
        assert rh_calls == []
        assert yahoo_calls == []


class TestFetchAllStillFatalsWhenEverySourceIsDown:
    def test_fetch_all_exits_when_no_source_serves(self, monkeypatch):
        monkeypatch.setattr(cri_scan, "_fetch_ib", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_uw", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_rh", lambda t: [])
        monkeypatch.setattr(cri_scan, "_fetch_yahoo", lambda t, days=400: [])
        monkeypatch.setattr(cri_scan.time, "sleep", lambda s: None)

        with pytest.raises(SystemExit):
            cri_scan.fetch_all(["SPY"])


class TestCriBudgetUnderBlackholedRobinhood:
    """REL-174 (R-481): one timed-out Robinhood POST opens the breaker for the
    process, so the post-close snapshot does not pay the rung again per quote."""

    def test_fetch_all_plus_snapshot_stays_inside_the_budget(self, monkeypatch, tmp_path):
        import time as _time

        import requests as _requests

        from clients import robinhood_client as rh

        token = tmp_path / "rh.json"
        token.write_text('{"access_token": "tok", "token_type": "Bearer"}')
        token.chmod(0o600)
        monkeypatch.setenv("ROBINHOOD_MCP_TOKEN_FILE", str(token))
        monkeypatch.delenv("ROBINHOOD_MCP_REFRESH_TOKEN", raising=False)
        monkeypatch.setattr(rh, "_refresh_disabled", False)
        if hasattr(rh, "_reset_process_state"):
            rh._reset_process_state()

        posts: list = []

        def blackholed(self, url, json=None, headers=None, timeout=None, **_kw):  # noqa: A002
            posts.append(url)
            _time.sleep(rh.LADDER_TIMEOUT_S)
            raise _requests.Timeout("read timed out")

        monkeypatch.setattr(rh.requests.Session, "post", blackholed)
        bars = [(f"2026-{m:02d}-{d:02d}", 100.0) for m in range(1, 13) for d in range(1, 22)]
        monkeypatch.setattr(cri_scan, "_fetch_ib", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_uw", lambda tickers: {})
        monkeypatch.setattr(cri_scan, "_fetch_cboe_cor1m", lambda: [])
        monkeypatch.setattr(cri_scan, "_fetch_yahoo", lambda t, days=400: list(bars))
        # time.sleep stays REAL here: the stub's 7s sleep is the blackhole.
        monkeypatch.setattr(cri_scan, "_fetch_ib_current_quote", lambda t: None)
        monkeypatch.setattr(cri_scan, "_fetch_yahoo_current_quote", lambda t: 1.0)
        monkeypatch.setattr(cri_scan, "fetch_cor1m_current_quote", lambda: 1.0)

        started = _time.monotonic()
        raw, _ = cri_scan.fetch_all(["SPY", "VIX", "VVIX", "COR1M"])
        snapshot = cri_scan.build_post_close_snapshot("2026-08-31", use_official_cboe_close=False)
        elapsed = _time.monotonic() - started
        assert "SPY" in raw and snapshot["SPY"] == 1.0
        assert elapsed < 20, f"{elapsed:.1f}s: the rung was paid again after the first timeout ({len(posts)} POSTs)"
        assert len(posts) == 1
