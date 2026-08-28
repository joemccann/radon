"""Tests for leap_iv_scanner.py and leap_scanner_uw.py — HV calculation, mispricing, delta."""
import math
import json
import sys
from contextlib import contextmanager, nullcontext
import pytest

from leap_iv_scanner import (
    calculate_historical_volatility,
    analyze_mispricing,
    find_strikes_by_delta,
    VolatilityData,
)
from leap_scanner_uw import (
    calculate_hv,
    approximate_delta,
    build_json_payload,
    pick_best_mispriced_leap,
    LeapOption,
    get_current_iv,
    get_leap_options,
    resolve_explicit_tickers,
    ScanResult,
    VolData,
)
import leap_scanner_uw


@pytest.fixture(autouse=True)
def _no_live_scan_ib(monkeypatch):
    @contextmanager
    def _none():
        yield None

    monkeypatch.setattr(leap_scanner_uw, "scan_ib_session", _none)


# ── calculate_historical_volatility (IB scanner) ────────────────────

class TestCalculateHistoricalVolatility:
    def test_constant_prices_zero_vol(self):
        prices = [100.0] * 30
        hv = calculate_historical_volatility(prices, 20)
        assert hv == 0.0

    def test_known_series(self):
        """Increasing 1% daily → known annualized vol."""
        # Need period+1 prices, so 21+ for period=20
        # Add some variance: alternate +1% and -1% daily returns
        import random
        random.seed(42)
        prices = [100.0]
        for _ in range(25):
            change = 1 + random.uniform(-0.02, 0.02)
            prices.append(prices[-1] * change)
        hv = calculate_historical_volatility(prices, 20)
        # Should have non-zero volatility
        assert hv > 5

    def test_insufficient_data_returns_zero(self):
        prices = [100, 101, 102]
        hv = calculate_historical_volatility(prices, 20)
        assert hv == 0.0

    def test_two_prices_returns_zero(self):
        prices = [100, 101]
        hv = calculate_historical_volatility(prices, 20)
        assert hv == 0.0

    def test_longer_period_uses_recent(self):
        # 100 prices, period=20 → uses last 21 prices
        prices = [100 + i * 0.5 for i in range(100)]
        hv = calculate_historical_volatility(prices, 20)
        assert hv > 0


# ── calculate_hv (UW scanner) ──────────────────────────────────────

class TestCalculateHvUw:
    def test_constant_prices_none_like(self):
        prices = [100.0] * 30
        hv = calculate_hv(prices, 20)
        # Zero returns → zero variance → 0.0
        assert hv == 0.0

    def test_insufficient_data_returns_none(self):
        prices = [100, 101]
        hv = calculate_hv(prices, 20)
        assert hv is None

    def test_valid_series(self):
        prices = [100 * (1.005 ** i) for i in range(25)]
        hv = calculate_hv(prices, 20)
        assert hv is not None
        assert hv > 0


# ── approximate_delta ───────────────────────────────────────────────

class TestApproximateDelta:
    def test_deep_itm(self):
        delta = approximate_delta(strike=80, price=100, iv=30, dte=365)
        assert delta == 0.8

    def test_atm(self):
        delta = approximate_delta(strike=100, price=100, iv=30, dte=365)
        assert delta == 0.5

    def test_slightly_otm(self):
        delta = approximate_delta(strike=110, price=100, iv=30, dte=365)
        assert delta == 0.35

    def test_far_otm(self):
        delta = approximate_delta(strike=140, price=100, iv=30, dte=365)
        assert delta == 0.1

    def test_zero_price(self):
        delta = approximate_delta(strike=100, price=0, iv=30, dte=365)
        assert delta == 0.5

    def test_zero_dte(self):
        delta = approximate_delta(strike=100, price=100, iv=30, dte=0)
        assert delta == 0.5


# ── analyze_mispricing ──────────────────────────────────────────────

class TestAnalyzeMispricing:
    def _make_vol_data(self, hv_20=40, hv_60=35, hv_252=30):
        return VolatilityData(
            ticker="XLK",
            sector="Technology",
            current_price=200,
            hv_20=hv_20,
            hv_60=hv_60,
            hv_252=hv_252,
        )

    def test_hv20_above_iv_mispriced(self):
        vol = self._make_vol_data(hv_20=45, hv_60=40, hv_252=35)
        option = {
            "iv": 25, "strike": 200, "expiry": "20270115",
            "bid": 10, "ask": 11, "mid": 10.5,
            "delta": 0.5, "vega": 0.30, "theta": -0.02,
            "oi": 1000, "volume": 50,
        }
        result = analyze_mispricing(option, vol, min_gap=15)
        assert result.is_mispriced is True
        assert result.hv_20_gap == 20.0  # 45 - 25
        assert result.mispricing_score > 0

    def test_hv_below_iv_not_mispriced(self):
        vol = self._make_vol_data(hv_20=20, hv_60=22, hv_252=25)
        option = {
            "iv": 30, "strike": 200, "expiry": "20270115",
            "bid": 10, "ask": 11, "mid": 10.5,
            "delta": 0.5, "vega": 0.30, "theta": -0.02,
            "oi": 1000, "volume": 50,
        }
        result = analyze_mispricing(option, vol, min_gap=15)
        assert result.is_mispriced is False
        assert result.hv_20_gap < 0

    def test_vega_boost_factor(self):
        vol = self._make_vol_data(hv_20=50, hv_60=45, hv_252=40)
        option_low_vega = {
            "iv": 25, "strike": 200, "expiry": "20270115",
            "bid": 10, "ask": 11, "mid": 10.5,
            "delta": 0.5, "vega": 0.10, "theta": -0.02,
        }
        option_high_vega = {
            "iv": 25, "strike": 200, "expiry": "20270115",
            "bid": 10, "ask": 11, "mid": 10.5,
            "delta": 0.5, "vega": 0.45, "theta": -0.02,
        }
        r_low = analyze_mispricing(option_low_vega, vol, min_gap=15)
        r_high = analyze_mispricing(option_high_vega, vol, min_gap=15)
        assert r_high.mispricing_score > r_low.mispricing_score


# ── explicit ticker-scan support (scan-by-ticker) ───────────────────

class TestResolveExplicitTickers:
    def test_merges_positional_and_comma_list(self):
        assert resolve_explicit_tickers(["nvda"], "amd, tsm") == ["NVDA", "AMD", "TSM"]

    def test_dedupes_across_sources(self):
        assert resolve_explicit_tickers(["MU", "mu"], "MU,AAPL") == ["MU", "AAPL"]

    def test_empty_inputs_yield_empty_list(self):
        assert resolve_explicit_tickers([], None) == []
        assert resolve_explicit_tickers(None, "") == []


class TestBuildJsonPayload:
    def test_stamps_universe_and_requested_tickers(self):
        payload = build_json_payload([], 10.0, "explicit", ["NVDA", "AMD"])
        assert payload["universe"] == "explicit"
        assert payload["requested_tickers"] == ["NVDA", "AMD"]
        assert payload["min_gap"] == 10.0
        assert payload["results"] == []
        assert payload["scan_time"]

    def test_preset_universe_stamp(self):
        payload = build_json_payload([], 15.0, "preset:mag7", ["AAPL"])
        assert payload["universe"] == "preset:mag7"

    def test_indexes_universe_stamp(self):
        payload = build_json_payload([], 10.0, "preset:indexes", ["NVDA", "AAPL"])
        assert payload["universe"] == "preset:indexes"


class TestPickBestMispricedLeap:
    def _leap(self, strike, iv, oi=100, expiry="2027-01-15"):
        return LeapOption(
            symbol=f"NVDA{expiry.replace('-', '')[2:]}C{int(strike * 1000):08d}",
            expiry=expiry,
            strike=strike,
            right="C",
            iv=iv,
            volume=10,
            oi=oi,
            delta_approx=0.5,
        )

    def test_picks_the_widest_gap_contract(self):
        leaps = [self._leap(200, 30.0), self._leap(210, 22.0), self._leap(220, 26.0)]
        assert pick_best_mispriced_leap(leaps, hv_20=45.0).strike == 210

    def test_breaks_iv_ties_on_open_interest(self):
        leaps = [self._leap(200, 22.0, oi=50), self._leap(210, 22.0, oi=900)]
        assert pick_best_mispriced_leap(leaps, hv_20=45.0).strike == 210

    def test_empty_group_has_no_contract(self):
        assert pick_best_mispriced_leap([], hv_20=45.0) is None


class TestBestLeapPayload:
    """The scanner page deep-links its best row into the chain order builder,
    so the payload has to name the contract — ticker + gap alone cannot."""

    def _result(self, best_leap):
        return ScanResult(
            ticker="NVDA",
            vol_data=VolData(
                ticker="NVDA", price=180.0, hv_20=45.0, hv_60=40.0, hv_252=35.0, avg_hv=40.0
            ),
            current_iv=25.0,
            iv_rank=30.0,
            leaps=[best_leap] if best_leap else [],
            best_gap=20.0,
            is_mispriced=True,
            best_leap=best_leap,
        )

    def test_emits_the_contract_the_order_builder_needs(self):
        leap = LeapOption(
            symbol="NVDA270115C00210000",
            expiry="2027-01-15",
            strike=210.0,
            right="C",
            iv=22.0,
            volume=12,
            oi=900,
            delta_approx=0.42,
        )
        payload = build_json_payload([self._result(leap)], 10.0, "explicit", ["NVDA"])
        best = payload["results"][0]["best_leap"]
        assert best["expiry"] == "2027-01-15"
        assert best["strike"] == 210.0
        assert best["right"] == "C"
        assert best["iv"] == 22.0
        assert best["gap"] == 23.0  # hv_20 45 - iv 22
        assert best["delta"] == 0.42
        assert best["oi"] == 900
        assert best["symbol"] == "NVDA270115C00210000"

    def test_absent_contract_serializes_as_null(self):
        payload = build_json_payload([self._result(None)], 10.0, "explicit", ["NVDA"])
        assert payload["results"][0]["best_leap"] is None


class TestResolveScanInputs:
    def test_indexes_uses_file_preset_and_stamps_universe(self, index_preset_dir):
        import leap_scanner_uw as leap
        from utils.presets import load_preset

        tickers, universe = leap.resolve_scan_inputs(explicit_tickers=[], preset="indexes")
        assert universe == "preset:indexes"
        assert "indexes" not in leap.PRESETS
        assert tickers == load_preset("indexes").tickers
        assert "NVDA" in tickers
        assert "AAPL" in tickers

    def test_explicit_tickers_win_over_preset(self):
        import leap_scanner_uw as leap

        tickers, universe = leap.resolve_scan_inputs(
            explicit_tickers=["nvda"], preset="indexes"
        )
        assert universe == "explicit"
        assert tickers == ["NVDA"]

    def test_workers_arg_defaults_to_16(self):
        import inspect
        import re

        import leap_scanner_uw as leap

        source = inspect.getsource(leap)
        assert re.search(
            r'add_argument\(\s*["\']--workers["\'][\s\S]*?default\s*=\s*16',
            source,
        )


def test_get_price_history_ib_hit_skips_uw_ohlc(monkeypatch):
    from types import SimpleNamespace

    class RecordingUW:
        def __init__(self):
            self.calls = []

        def get_stock_ohlc(self, ticker, candle_size="1d"):
            self.calls.append((ticker, candle_size))
            return {"data": [{"close": 1.0}] * 60}

    class RecordingIB:
        def get_historical_data(self, contract, **kwargs):
            return [SimpleNamespace(date="2026-01-01", close=100.0 + i) for i in range(60)]

    uw = RecordingUW()
    monkeypatch.setattr(
        leap_scanner_uw,
        "get_yahoo_history",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("yahoo")),
    )

    prices = leap_scanner_uw.get_price_history("AAPL", uw_client=uw, ib=RecordingIB())

    assert uw.calls == []
    assert len(prices) == 60
    assert prices[0] == 100.0
    assert prices[-1] == 159.0


def test_get_uw_history_ib_miss_calls_uw_ohlc(monkeypatch):
    from types import SimpleNamespace

    class RecordingUW:
        def __init__(self):
            self.calls = []

        def get_stock_ohlc(self, ticker, candle_size="1d"):
            self.calls.append((ticker, candle_size))
            return {"data": [{"close": float(i)} for i in range(1, 61)]}

    class RecordingIB:
        def get_historical_data(self, contract, **kwargs):
            return [SimpleNamespace(date="2026-01-01", close=100.0)]

    uw = RecordingUW()
    monkeypatch.setattr(
        leap_scanner_uw,
        "get_yahoo_history",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("yahoo")),
    )

    prices = leap_scanner_uw.get_price_history("MSFT", uw_client=uw, ib=RecordingIB())

    assert uw.calls == [("MSFT", "1d")]
    assert prices == [float(i) for i in range(1, 61)]


def test_all_provider_failures_preserve_cache_and_fail_health(tmp_path, monkeypatch):
    cache = tmp_path / "leap.json"
    previous = {"scan_time": "old", "results": [{"ticker": "SPY"}]}
    cache.write_text(json.dumps(previous))
    monkeypatch.setattr(leap_scanner_uw, "DASHBOARD_CACHE_PATH", cache)
    monkeypatch.setattr(leap_scanner_uw, "UWClient", lambda: nullcontext(object()))
    monkeypatch.setattr(leap_scanner_uw, "scan_ticker", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        sys,
        "argv",
        ["leap_scanner_uw.py", "SPY", "QQQ", "--json", "--output", str(tmp_path / "report.html")],
    )

    assert leap_scanner_uw.main() == 1
    assert json.loads(cache.read_text()) == previous


def _one_scan_result(ticker: str) -> ScanResult:
    return ScanResult(
        ticker=ticker,
        vol_data=VolData(
            ticker=ticker, price=100.0, hv_20=20.0, hv_60=18.0, hv_252=16.0, avg_hv=18.0
        ),
        current_iv=15.0,
        iv_rank=40.0,
        leaps=[],
        best_gap=5.0,
        is_mispriced=False,
    )


def test_partial_ticker_failures_write_cache_and_exit_zero(tmp_path, monkeypatch):
    """Production 2026-08-20: 347/518 names scanned, 171 with no LEAPs or a
    provider miss. main() still wrote data/leap.json + an ok heartbeat, then
    exited 1 because failed_tickers was nonempty. FastAPI mapped that to 502
    and radon-leap.service paged P1.
    """
    cache = tmp_path / "leap.json"
    monkeypatch.setattr(leap_scanner_uw, "DASHBOARD_CACHE_PATH", cache)
    monkeypatch.setattr(leap_scanner_uw, "UWClient", lambda: nullcontext(object()))
    monkeypatch.setattr(leap_scanner_uw, "mirror_scan_snapshot", lambda *a, **k: None)

    # REL-049 / R-095: the universe proportions matter now. A name with no
    # LEAPs is ordinary; a provider exception is not, and once provider
    # failures dominate the run the scan is an outage. The original 3-ticker
    # fixture was 1 result / 1 empty / 1 provider error, i.e. a 50% provider
    # failure rate, which is the exhausted case — not the 347/518 production
    # shape this test is named for.
    scanned = [f"T{i}" for i in range(8)]

    def fake_scan(ticker, *args, **kwargs):
        if ticker in scanned:
            return _one_scan_result(ticker)
        if ticker == "QQQ":
            return None
        raise RuntimeError("uw miss")

    monkeypatch.setattr(leap_scanner_uw, "scan_ticker", fake_scan)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "leap_scanner_uw.py",
            *scanned,
            "QQQ",
            "BA",
            "--json",
            "--output",
            str(tmp_path / "report.html"),
        ],
    )

    assert leap_scanner_uw.main() == 0
    payload = json.loads(cache.read_text())
    assert sorted(row["ticker"] for row in payload["results"]) == sorted(scanned)
    assert set(payload["failed_tickers"]) == {"QQQ", "BA"}
    assert payload["provider_failures"] == ["BA"]
    assert payload["status"] == "ok"


def test_a_provider_wipeout_exits_nonzero_and_marks_the_row_error(tmp_path, monkeypatch):
    """R-095: UW's daily cap tripping after the third ticker yielded
    results=2 / failed=516, an overwritten data/leap.json, an unconditional
    `leap-scan = ok` heartbeat and exit 0 — a 2-name LEAP tab behind a green
    banner."""
    cache = tmp_path / "leap.json"
    monkeypatch.setattr(leap_scanner_uw, "DASHBOARD_CACHE_PATH", cache)
    monkeypatch.setattr(leap_scanner_uw, "UWClient", lambda: nullcontext(object()))
    health: list = []
    monkeypatch.setattr(
        leap_scanner_uw,
        "mirror_scan_snapshot",
        lambda *a, **k: health.append(k.get("health_error")),
    )

    def fake_scan(ticker, *args, **kwargs):
        if ticker in {"SPY", "QQQ"}:
            return _one_scan_result(ticker)
        raise leap_scanner_uw.UWRateLimitError("daily cap")

    monkeypatch.setattr(leap_scanner_uw, "scan_ticker", fake_scan)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "leap_scanner_uw.py",
            "SPY", "QQQ", *[f"X{i}" for i in range(20)],
            "--json", "--output", str(tmp_path / "report.html"),
        ],
    )

    assert leap_scanner_uw.main() == 1
    payload = json.loads(cache.read_text())
    assert payload["status"] == "degraded"
    assert len(payload["provider_failures"]) == 20
    assert health and health[-1] is not None
    assert health[-1]["class"] == "provider_exhausted"


# ── find_strikes_by_delta ───────────────────────────────────────────

class TestFindStrikesByDelta:
    def test_finds_closest_match(self):
        options = [
            {"delta": 0.52, "strike": 100},
            {"delta": 0.31, "strike": 110},
            {"delta": 0.19, "strike": 120},
        ]
        result = find_strikes_by_delta(options, [0.50, 0.30, 0.20], 100)
        assert 0.50 in result
        assert result[0.50]["strike"] == 100
        assert 0.30 in result
        assert result[0.30]["strike"] == 110

    def test_no_match_within_threshold(self):
        options = [
            {"delta": 0.80, "strike": 80},
        ]
        result = find_strikes_by_delta(options, [0.50], 100)
        assert 0.50 not in result

    def test_none_delta_skipped(self):
        options = [
            {"delta": None, "strike": 100},
            {"delta": 0.49, "strike": 105},
        ]
        result = find_strikes_by_delta(options, [0.50], 100)
        assert 0.50 in result
        assert result[0.50]["strike"] == 105


# ── null-tolerant UW field coercion (2026-07-05 regression) ─────────
# UW returns keys PRESENT with null values (ETF chains, weekends), so
# dict.get(key, 0) hands None to float()/int() and the TypeError is not
# caught by the loop's (ValueError, IndexError) handler - it killed the
# whole ticker ("Error scanning XLU: float() argument ... 'NoneType'").

class _FakeUW:
    def __init__(self, contracts=None, iv_data=None):
        self._contracts = contracts
        self._iv_data = iv_data

    def get_option_contracts(self, ticker):
        return {"data": self._contracts or []}

    def get_iv_rank(self, ticker):
        return {"data": self._iv_data or []}


class TestNullUwFields:
    def test_null_implied_vol_contract_skipped_not_fatal(self):
        contracts = [
            {  # null IV: must be skipped, not raise TypeError
                "option_symbol": "XLU   270115C00045000",
                "implied_volatility": None,
                "volume": None,
                "open_interest": None,
            },
            {  # healthy contract survives
                "option_symbol": "XLU   270115C00050000",
                "implied_volatility": 0.14,
                "volume": 12,
                "open_interest": 340,
            },
        ]
        leaps = get_leap_options("XLU", min_year=2027, _client=_FakeUW(contracts=contracts))
        assert [l.strike for l in leaps] == [50.0]

    def test_null_volume_and_oi_coerce_to_zero(self):
        contracts = [
            {
                "option_symbol": "XLU   270115C00050000",
                "implied_volatility": 0.14,
                "volume": None,
                "open_interest": None,
            },
        ]
        leaps = get_leap_options("XLU", min_year=2027, _client=_FakeUW(contracts=contracts))
        assert len(leaps) == 1
        assert leaps[0].volume == 0
        assert leaps[0].oi == 0

    def test_null_iv_rank_fields_return_zero(self):
        iv, rank = get_current_iv(
            "XLU", _client=_FakeUW(iv_data=[{"volatility": None, "iv_rank_1y": None}])
        )
        assert iv == 0
        assert rank == 0
