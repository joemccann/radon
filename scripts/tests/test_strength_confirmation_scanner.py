"""Regression tests for strength_confirmation_scanner.py."""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import strength_confirmation_scanner as strength


@pytest.fixture(autouse=True)
def _no_live_scan_ib(monkeypatch) -> None:
    @contextmanager
    def _none():
        yield None

    monkeypatch.setattr(strength, "scan_ib_session", _none)


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


class _RecordingUW:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get_stock_ohlc(self, ticker: str, candle_size: str = "1d"):
        self.calls.append(("ohlc", ticker, candle_size))
        return {"data": [{"close": 100.0}]}

    def get_iv_rank(self, ticker: str):
        self.calls.append(("iv_rank", ticker))
        return {"data": []}

    def get_option_contracts(self, ticker: str, **kwargs):
        self.calls.append(("contracts", ticker, kwargs))
        return {"data": []}

    def get_greek_exposure_by_strike(self, ticker: str):
        self.calls.append(("gex_strike", ticker))
        return {"data": []}

    def get_stock_oi_change(self, ticker: str, **_kwargs):
        self.calls.append(("oi_change", ticker))
        return {"data": []}


class _RecordingIB:
    def __init__(self, bars) -> None:
        self.calls: list[tuple] = []
        self.bars = bars

    def get_historical_data(self, contract, **kwargs):
        self.calls.append((contract, kwargs))
        return self.bars


def _ib_closes(count: int):
    return [SimpleNamespace(date=f"2026-01-{(i % 28) + 1:02d}", close=100.0 + i) for i in range(count)]


def test_scan_ticker_ib_hit_skips_uw_ohlc() -> None:
    from utils.uw_surface import MIN_DAILY_CLOSES

    uw = _RecordingUW()
    ib = _RecordingIB(bars=_ib_closes(MIN_DAILY_CLOSES))

    strength.scan_ticker("AAPL", client=uw, ib=ib, market_context=_market_context())

    assert [name for name, *_rest in uw.calls] != []
    assert all(name != "ohlc" for name, *_rest in uw.calls)
    assert len(ib.calls) == 1


def test_scan_ticker_ib_miss_calls_uw_ohlc() -> None:
    uw = _RecordingUW()
    ib = _RecordingIB(bars=_ib_closes(3))

    strength.scan_ticker("AAPL", client=uw, ib=ib, market_context=_market_context())

    assert uw.calls[0] == ("ohlc", "AAPL", "1d")


def test_scan_ticker_reraises_rate_limit_from_ohlc(monkeypatch) -> None:
    def _rate_limited(_client, _ticker, **_kw):
        raise strength.UWRateLimitError("daily request limit")

    monkeypatch.setattr(strength, "fetch_surface", _rate_limited)
    with pytest.raises(strength.UWRateLimitError):
        strength.scan_ticker("AAPL", client=object())


def test_scan_universe_budget_block_skips_workers_and_keeps_last_good(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "strength_confirmation.json"
    monkeypatch.setattr(strength, "_CACHE_PATH", cache)
    monkeypatch.setattr(strength, "should_block_universe_scan", lambda: True)
    monkeypatch.setattr(strength, "resolve_tickers", lambda *_a, **_k: (["AAPL", "MSFT"], "preset:ndx100"))
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
    cache.write_text(json.dumps(good))

    def boom(*_a, **_k):
        raise AssertionError("universe workers must not run when budget is blocked")

    monkeypatch.setattr(strength, "UWClient", boom)
    monkeypatch.setattr(strength, "scan_ticker", boom)
    payload = strength.scan_universe([])
    assert payload["candidates_found"] == 1
    assert payload["results"][0]["ticker"] == "AAPL"
    assert strength.save_cache(payload, cache) is True
    stored = json.loads(cache.read_text())
    assert stored["candidates_found"] == 1


def test_scan_universe_budget_block_degraded_when_no_last_good(tmp_path, monkeypatch) -> None:
    cache = tmp_path / "strength_confirmation.json"
    monkeypatch.setattr(strength, "_CACHE_PATH", cache)
    monkeypatch.setattr(strength, "should_block_universe_scan", lambda: True)
    monkeypatch.setattr(strength, "resolve_tickers", lambda *_a, **_k: (["AAPL", "MSFT"], "preset:ndx100"))

    def boom(*_a, **_k):
        raise AssertionError("UW must not run when budget is blocked")

    monkeypatch.setattr(strength, "UWClient", boom)
    monkeypatch.setattr(strength, "scan_ticker", boom)
    payload = strength.scan_universe([])
    assert payload["candidates_found"] == 0
    assert payload["coverage"]["tickers"] == 2
    assert payload["coverage"]["completed"] == 0
    assert strength.save_cache(payload, cache) is False
    assert not cache.exists()


def test_scan_universe_explicit_tickers_bypass_budget_block(monkeypatch) -> None:
    monkeypatch.setattr(strength, "should_block_universe_scan", lambda: True)
    seen: list[str] = []

    class FakeClient:
        def close(self):
            return None

    monkeypatch.setattr(strength, "UWClient", lambda **_k: FakeClient())
    monkeypatch.setattr(strength, "build_market_context", lambda _client: object())

    def fake_scan(ticker, *_a, **_k):
        seen.append(ticker)
        return None

    monkeypatch.setattr(strength, "scan_ticker", fake_scan)
    payload = strength.scan_universe(["MU"])
    assert seen == ["MU"]
    assert payload["universe"] == "explicit"
    assert payload["coverage"]["no_setup"] == 1


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


def _strength_surface() -> dict:
    closes = [85 + i * 0.22 for i in range(70)]
    return {
        "ohlc": {"data": [{"close": close} for close in closes]},
        "iv_rank": {"data": [{"date": "2026-06-24", "volatility": 0.22, "iv_rank_1y": 24}]},
        "contracts": {
            "data": [
                {"option_symbol": "AAPL260717P00100000", "implied_volatility": 0.28, "delta": -0.25},
                {"option_symbol": "AAPL260717C00120000", "implied_volatility": 0.30, "delta": 0.25},
                {"option_symbol": "AAPL260717C00130000", "implied_volatility": 0.34, "delta": 0.10},
            ]
        },
        "gex_strike": {
            "data": [
                {"strike": 105, "call_gex": 500_000, "put_gex": -180_000},
                {"strike": 110, "call_gex": 900_000, "put_gex": -70_000},
                {"strike": 115, "call_gex": 650_000, "put_gex": -20_000},
            ]
        },
    }


def _flow_alerts() -> dict:
    return {
        "data": [
            {"option_type": "call", "ask_side": True, "premium": 500_000},
            {"option_type": "call", "side": "ask", "premium": 400_000},
            {"option_type": "put", "bid_side": True, "premium": 150_000},
        ]
    }


def _options_volume() -> dict:
    return {"data": [{"call_premium": 1_200_000, "put_premium": 350_000}]}


def _aggregate_gex() -> dict:
    return {
        "data": [
            {"date": "2026-06-22", "call_gex": 1_000_000, "put_gex": -1_400_000},
            {"date": "2026-06-23", "call_gex": 1_300_000, "put_gex": -1_100_000},
            {"date": "2026-06-24", "call_gex": 1_800_000, "put_gex": -900_000},
        ]
    }


def _oi_change() -> dict:
    return {
        "data": [
            {"strike": 110, "expiry": "2026-07-17", "option_type": "call", "oi_change": 1200},
            {"strike": 115, "expiry": "2026-07-17", "option_type": "call", "oi_change": 900},
            {"strike": 120, "expiry": "2026-08-21", "option_type": "call", "oi_change": 700},
        ]
    }


def _term_structure() -> dict:
    return {
        "data": [
            {"expiry": "2026-07-17", "volatility": 0.20},
            {"expiry": "2026-08-21", "volatility": 0.24},
            {"expiry": "2026-09-18", "volatility": 0.25},
        ]
    }


def _risk_reversal() -> dict:
    return {
        "data": [
            {"date": "2026-06-20", "value": 0.08},
            {"date": "2026-06-24", "value": 0.03},
        ]
    }


class OIChangeClient:
    def get_stock_oi_change(self, ticker: str, **_kwargs):
        assert ticker == "AAPL"
        return _oi_change()


_DROPPED_UW_METHODS = (
    "get_stock_flow_alerts",
    "get_options_volume",
    "get_greek_exposure",
    "get_volatility_term_structure",
    "get_historical_risk_reversal_skew",
)


class RecordingSlimClient:
    """Client that records dropped UW methods so tests can forbid them."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):
        if name in _DROPPED_UW_METHODS:
            def _record(*_args, **_kwargs):
                self.calls.append(name)
                return {"data": []}

            return _record
        raise AttributeError(name)

    def get_stock_oi_change(self, ticker: str, **_kwargs):
        self.calls.append("get_stock_oi_change")
        assert ticker == "AAPL"
        return _oi_change()


def test_verdict_for_thresholds() -> None:
    assert strength.verdict_for(7) == "REAL_STRENGTH_CONFIRMED"
    assert strength.verdict_for(5) == "WATCHLIST"
    assert strength.verdict_for(4) == "WEAK"


def test_scan_ticker_does_not_call_dropped_uw_methods(monkeypatch) -> None:
    client = RecordingSlimClient()
    monkeypatch.setattr(strength, "fetch_surface", lambda _client, _ticker, **_kw: _strength_surface())
    candidate = strength.scan_ticker(
        "AAPL",
        client=client,
        now=datetime(2026, 6, 24, tzinfo=timezone.utc),
        market_context=_market_context(),
    )

    assert candidate is not None
    assert [factor.group for factor in candidate.factors] == [
        "Q-SCORES",
        "NET GEX",
        "CALL POSITIONING",
        "TERM STRUCTURE",
        "VOLATILITY SMILE",
        "SYSTEMATIC POSITIONING",
        "MARKET BREADTH",
    ]
    assert "get_stock_oi_change" in client.calls
    assert [name for name in client.calls if name in _DROPPED_UW_METHODS] == []


def test_seven_group_scoring_confirms_when_payloads_supplied() -> None:
    surface = _strength_surface()
    prices = [row["close"] for row in surface["ohlc"]["data"]]
    spot = prices[-1]
    iv, iv_rank = strength._latest_iv(surface["iv_rank"])
    context = _market_context()
    factors = [
        strength.analyze_q_scores(prices, iv, iv_rank, strength._option_flow_summary(_flow_alerts(), _options_volume())),
        strength.analyze_net_gex(surface["gex_strike"], _aggregate_gex(), spot),
        strength.analyze_call_positioning(surface["gex_strike"], _oi_change(), spot),
        strength.analyze_term_structure(_term_structure(), context),
        strength.analyze_vol_smile(surface["contracts"], _risk_reversal()),
        strength.analyze_systematic_positioning(context),
        strength.analyze_market_breadth(context, prices),
    ]
    groups_passed = sum(1 for factor in factors if factor.passed)
    factor_map = {factor.group: factor for factor in factors}

    assert [factor.group for factor in factors] == [
        "Q-SCORES",
        "NET GEX",
        "CALL POSITIONING",
        "TERM STRUCTURE",
        "VOLATILITY SMILE",
        "SYSTEMATIC POSITIONING",
        "MARKET BREADTH",
    ]
    assert groups_passed == 7
    assert strength.verdict_for(groups_passed) == "REAL_STRENGTH_CONFIRMED"
    assert factor_map["Q-SCORES"].passed is True
    assert factor_map["SYSTEMATIC POSITIONING"].source == "APPROX"
    assert factor_map["MARKET BREADTH"].source in {"UW", "APPROX"}


def test_scan_ticker_returns_watchlist_when_dropped_sources_degrade(monkeypatch) -> None:
    monkeypatch.setattr(strength, "fetch_surface", lambda _client, _ticker, **_kw: _strength_surface())
    candidate = strength.scan_ticker(
        "AAPL",
        client=OIChangeClient(),
        now=datetime(2026, 6, 24, tzinfo=timezone.utc),
        market_context=_market_context(),
    )

    assert candidate is not None
    assert candidate.verdict == "WATCHLIST"
    assert candidate.groups_passed == 5
    factor_map = {factor.group: factor for factor in candidate.factors}
    assert factor_map["Q-SCORES"].passed is False
    assert factor_map["NET GEX"].passed is False
    assert factor_map["CALL POSITIONING"].passed is True
    assert factor_map["TERM STRUCTURE"].passed is True


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
