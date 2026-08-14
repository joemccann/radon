"""Regression tests for strength_confirmation_scanner.py."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import strength_confirmation_scanner as strength


def test_dedupe_tickers_rejects_numeric_noise() -> None:
    assert strength.dedupe_tickers(["AAPL", "aapl", "2026", "MSFT"]) == ["AAPL", "MSFT"]


def test_resolve_explicit_tickers_overrides_preset() -> None:
    tickers, source = strength.resolve_tickers(["mu", "MU", "2026"], "ndx100")

    assert tickers == ["MU"]
    assert source == "explicit"


def test_build_output_counts_confirmed_strength_only_when_all_groups_pass() -> None:
    confirmed = strength.StrengthCandidate(
        ticker="AAPL",
        verdict="REAL_STRENGTH_CONFIRMED",
        score=100,
        groups_passed=7,
        spot=100,
        factors=[],
        errors=[],
    )
    watch = strength.StrengthCandidate(
        ticker="MSFT",
        verdict="WATCHLIST",
        score=72,
        groups_passed=5,
        spot=300,
        factors=[],
        errors=[],
    )

    payload = strength.build_output([watch, confirmed], "explicit", 2, requested_tickers=["AAPL", "MSFT"])

    assert payload["confirmed_strength_count"] == 1
    assert payload["candidates_found"] == 2
    assert payload["requested_tickers"] == ["AAPL", "MSFT"]
    assert payload["results"][0]["ticker"] == "AAPL"


def test_scan_ticker_reraises_rate_limit_from_ohlc() -> None:
    class RateLimitedOhlc:
        def get_stock_ohlc(self, ticker: str, candle_size: str = "1d"):
            raise strength.UWRateLimitError("daily request limit")

    with pytest.raises(strength.UWRateLimitError):
        strength.scan_ticker("AAPL", client=RateLimitedOhlc())


def test_save_cache_preserves_last_good_on_empty_scan(tmp_path, monkeypatch) -> None:
    mirrored: list[object] = []
    monkeypatch.setattr(strength, "mirror_scan_snapshot", lambda *a, **_k: mirrored.append(a))
    path = tmp_path / "strength_confirmation.json"
    good = strength.build_output(
        [
            strength.StrengthCandidate(
                ticker="AAPL",
                verdict="REAL_STRENGTH_CONFIRMED",
                score=100,
                groups_passed=7,
                spot=100,
                factors=[],
                errors=[],
            )
        ],
        "preset:ndx100",
        102,
        requested_tickers=["AAPL"],
    )
    assert strength.save_cache(good, path) is True
    empty = strength.build_output([], "preset:ndx100", 102, requested_tickers=["AAPL"])
    empty["coverage"] = {
        "tickers": 102, "ok": 0, "no_setup": 0, "rate_limited": 102, "errors": 0, "completed": 0,
    }
    assert strength.save_cache(empty, path) is False
    stored = json.loads(path.read_text())
    assert stored["candidates_found"] == 1
    assert stored["results"][0]["ticker"] == "AAPL"
    assert len(mirrored) == 1


def test_resolve_ndx100_uses_fallback_when_preset_is_structurally_corrupt(monkeypatch) -> None:
    import utils.presets as presets

    loaded = SimpleNamespace(name="ndx100", tickers=["1985", "2024", "2025"])
    monkeypatch.setattr(presets, "load_preset", lambda _name: loaded)

    tickers, source = strength.resolve_tickers([], "ndx100")

    assert source == "fallback:ndx100"
    assert "AAPL" in tickers
    assert "NVDA" in tickers
    assert "1985" not in tickers


class BullishClient:
    def get_stock_ohlc(self, ticker: str, candle_size: str = "1d"):
        assert ticker == "AAPL"
        assert candle_size == "1d"
        closes = [85 + i * 0.22 for i in range(70)]
        return {"data": [{"close": close} for close in closes]}

    def get_stock_flow_alerts(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return {
            "data": [
                {"option_type": "call", "ask_side": True, "premium": 500_000},
                {"option_type": "call", "side": "ask", "premium": 400_000},
                {"option_type": "put", "bid_side": True, "premium": 150_000},
            ]
        }

    def get_options_volume(self, ticker: str):
        assert ticker == "AAPL"
        return {"data": [{"call_premium": 1_200_000, "put_premium": 350_000}]}

    def get_iv_rank(self, ticker: str):
        assert ticker == "AAPL"
        return {"data": [{"date": "2026-06-24", "volatility": 0.22, "iv_rank_1y": 24}]}

    def get_greek_exposure_by_strike(self, ticker: str):
        assert ticker == "AAPL"
        return {
            "data": [
                {"strike": 105, "call_gex": 500_000, "put_gex": -180_000},
                {"strike": 110, "call_gex": 900_000, "put_gex": -70_000},
                {"strike": 115, "call_gex": 650_000, "put_gex": -20_000},
            ]
        }

    def get_greek_exposure(self, ticker: str):
        assert ticker == "AAPL"
        return {
            "data": [
                {"date": "2026-06-22", "call_gex": 1_000_000, "put_gex": -1_400_000},
                {"date": "2026-06-23", "call_gex": 1_300_000, "put_gex": -1_100_000},
                {"date": "2026-06-24", "call_gex": 1_800_000, "put_gex": -900_000},
            ]
        }

    def get_stock_oi_change(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return {
            "data": [
                {"strike": 110, "expiry": "2026-07-17", "option_type": "call", "oi_change": 1200},
                {"strike": 115, "expiry": "2026-07-17", "option_type": "call", "oi_change": 900},
                {"strike": 120, "expiry": "2026-08-21", "option_type": "call", "oi_change": 700},
            ]
        }

    def get_volatility_term_structure(self, ticker: str):
        assert ticker == "AAPL"
        return {
            "data": [
                {"expiry": "2026-07-17", "volatility": 0.20},
                {"expiry": "2026-08-21", "volatility": 0.24},
                {"expiry": "2026-09-18", "volatility": 0.25},
            ]
        }

    def get_option_contracts(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return {
            "data": [
                {"option_symbol": "AAPL260717P00100000", "implied_volatility": 0.28, "delta": -0.25},
                {"option_symbol": "AAPL260717C00120000", "implied_volatility": 0.30, "delta": 0.25},
                {"option_symbol": "AAPL260717C00130000", "implied_volatility": 0.34, "delta": 0.10},
            ]
        }

    def get_historical_risk_reversal_skew(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return {
            "data": [
                {"date": "2026-06-20", "value": 0.08},
                {"date": "2026-06-24", "value": 0.03},
            ]
        }

    def get_stock_info(self, ticker: str):
        assert ticker == "AAPL"
        return {"data": {"sector": "Technology"}}

    def get_sector_etfs(self):
        return {
            "data": [
                {"ticker": "XLK", "change": 1.2, "call_premium": 2_000_000, "put_premium": 600_000},
                {"ticker": "XLF", "change": 0.4, "call_premium": 1_200_000, "put_premium": 900_000},
                {"ticker": "XLV", "change": 0.3, "call_premium": 1_100_000, "put_premium": 800_000},
                {"ticker": "XLY", "change": 0.6, "call_premium": 1_000_000, "put_premium": 700_000},
            ]
        }


def test_scan_ticker_confirms_real_strength_when_all_groups_pass() -> None:
    candidate = strength.scan_ticker(
        "AAPL",
        client=BullishClient(),
        now=datetime(2026, 6, 24, tzinfo=timezone.utc),
        market_context=strength.MarketContext(
            vix_front=17.0,
            vix_back=19.5,
            vix_front_prev=18.0,
            vix_back_prev=19.7,
            cta_exposure_pct=130.0,
            cta_forced_reduction_pct=0.0,
            spx_distance_pct=2.0,
            sectors_positive_ratio=0.75,
            sectors_call_premium_ratio=0.67,
            notes=[],
        ),
    )

    assert candidate is not None
    assert candidate.verdict == "REAL_STRENGTH_CONFIRMED"
    assert candidate.groups_passed == 7
    assert candidate.score == 100
    factor_map = {factor.group: factor for factor in candidate.factors}
    assert factor_map["Q-SCORES"].passed is True
    assert factor_map["SYSTEMATIC POSITIONING"].source == "APPROX"
    assert factor_map["MARKET BREADTH"].source in {"UW", "APPROX"}


def test_scan_ticker_returns_watchlist_when_one_group_fails() -> None:
    weak_context = strength.MarketContext(
        vix_front=20.0,
        vix_back=18.5,
        vix_front_prev=19.5,
        vix_back_prev=18.6,
        cta_exposure_pct=130.0,
        cta_forced_reduction_pct=0.0,
        spx_distance_pct=2.0,
        sectors_positive_ratio=0.75,
        sectors_call_premium_ratio=0.67,
        notes=[],
    )

    candidate = strength.scan_ticker(
        "AAPL",
        client=BullishClient(),
        now=datetime(2026, 6, 24, tzinfo=timezone.utc),
        market_context=weak_context,
    )

    assert candidate is not None
    assert candidate.verdict == "WATCHLIST"
    assert candidate.groups_passed == 6
    assert {factor.group: factor for factor in candidate.factors}["TERM STRUCTURE"].passed is False


# ── measured breadth wiring (data/breadth.json → MARKET BREADTH) ──


def _market_context(**overrides):
    base = dict(
        vix_front=17.0,
        vix_back=19.5,
        vix_front_prev=18.0,
        vix_back_prev=19.7,
        cta_exposure_pct=130.0,
        cta_forced_reduction_pct=0.0,
        spx_distance_pct=2.0,
        sectors_positive_ratio=0.75,
        sectors_call_premium_ratio=0.67,
        notes=[],
    )
    base.update(overrides)
    return strength.MarketContext(**base)


def test_analyze_market_breadth_uses_measured_net_breadth_when_present() -> None:
    factor = strength.analyze_market_breadth(
        _market_context(net_breadth_20d=3250.0), prices=[100.0] * 30
    )

    check = {c.label: c for c in factor.checks}["Advancing stocks expanding"]
    assert check.passed is True
    assert check.value == pytest.approx(3250.0)
    assert check.source == "IB"


def test_analyze_market_breadth_measured_negative_overrides_passing_proxy() -> None:
    rising = [100.0 + i for i in range(30)]

    factor = strength.analyze_market_breadth(
        _market_context(net_breadth_20d=-1200.0), prices=rising
    )

    check = {c.label: c for c in factor.checks}["Advancing stocks expanding"]
    assert check.passed is False
    assert check.source == "IB"


def test_analyze_market_breadth_falls_back_to_proxy_without_breadth() -> None:
    rising = [100.0 + i for i in range(30)]

    factor = strength.analyze_market_breadth(_market_context(), prices=rising)

    check = {c.label: c for c in factor.checks}["Advancing stocks expanding"]
    assert check.passed is True
    assert check.source == "APPROX"


def test_read_latest_breadth_serves_fresh_and_rejects_stale(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(strength, "_DATA_DIR", tmp_path)
    now = datetime.now(timezone.utc)

    fresh_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    (tmp_path / "breadth.json").write_text(
        json.dumps({"latest": {"session_date": fresh_date, "cum_ad_change_20d": 1500.0}})
    )
    assert strength._read_latest_breadth(now) == pytest.approx(1500.0)

    stale_date = (now - timedelta(days=10)).strftime("%Y-%m-%d")
    (tmp_path / "breadth.json").write_text(
        json.dumps({"latest": {"session_date": stale_date, "cum_ad_change_20d": 1500.0}})
    )
    assert strength._read_latest_breadth(now) is None


def test_read_latest_breadth_none_when_missing_or_malformed(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(strength, "_DATA_DIR", tmp_path)
    now = datetime.now(timezone.utc)

    assert strength._read_latest_breadth(now) is None

    (tmp_path / "breadth.json").write_text("not json")
    assert strength._read_latest_breadth(now) is None

    (tmp_path / "breadth.json").write_text(json.dumps({"latest": {"session_date": "bad", "cum_ad_change_20d": 1.0}}))
    assert strength._read_latest_breadth(now) is None


def test_build_market_context_populates_net_breadth(monkeypatch) -> None:
    monkeypatch.setattr(strength, "_read_latest_cri", lambda: {})
    monkeypatch.setattr(strength, "_read_latest_breadth", lambda now=None: 2100.0)

    class SectorClient:
        def get_sector_etfs(self):
            return {"data": []}

    context = strength.build_market_context(SectorClient())

    assert context.net_breadth_20d == pytest.approx(2100.0)
