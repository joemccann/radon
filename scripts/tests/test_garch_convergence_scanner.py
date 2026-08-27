"""Regression tests for garch_convergence.py explicit ticker-scan support."""
from __future__ import annotations

from contextlib import contextmanager

import pytest

import garch_convergence as garch


@pytest.fixture(autouse=True)
def _no_live_scan_ib(monkeypatch):
    @contextmanager
    def _none():
        yield None

    monkeypatch.setattr(garch, "scan_ib_session", _none)


def test_resolve_inputs_pairs_explicit_tickers_consecutively():
    tickers, pairs, description, _driver = garch.resolve_inputs(
        ["NVDA", "AMD", "TSM", "ASML"], None
    )

    assert pairs == [["NVDA", "AMD"], ["TSM", "ASML"]]
    assert tickers == ["NVDA", "AMD", "TSM", "ASML"]
    assert description == "Ad-hoc pairs"


def test_to_json_stamps_universe_and_requested_tickers():
    payload = garch.to_json(
        {}, [], universe="explicit", requested_tickers=["NVDA", "AMD"]
    )

    assert payload["universe"] == "explicit"
    assert payload["requested_tickers"] == ["NVDA", "AMD"]
    assert payload["tickers"] == {}
    assert payload["pairs"] == []
    assert payload["scan_time"]


def test_to_json_defaults_keep_envelope_shape():
    payload = garch.to_json({}, [])

    assert payload["universe"] == ""
    assert payload["requested_tickers"] == []


def _consecutive_pairs(tickers):
    return [[tickers[i], tickers[i + 1]] for i in range(0, len(tickers) - 1, 2)]


def test_resolve_inputs_indexes_uses_curated_pairs_not_consecutive(index_preset_dir):
    from utils.presets import load_preset

    tickers, pairs, description, _driver = garch.resolve_inputs(None, "indexes")

    assert len(pairs) >= 700
    assert all(isinstance(pair, list) and len(pair) == 2 for pair in pairs)
    preset = load_preset("indexes")
    assert [frozenset(pair) for pair in pairs] == [frozenset(pair) for pair in preset.pairs]
    assert pairs != _consecutive_pairs(tickers)

    desc = description.lower()
    assert any(token in desc for token in ("nasdaq", "ndx", "nasdaq-100", "nasdaq 100"))
    assert any(token in desc for token in ("s&p", "sp500", "s&p 500", "sp 500"))
    assert any(token in desc for token in ("russell", "r2k", "russell 2000"))


def test_resolve_inputs_indexes_passes_vol_driver_gate(index_preset_dir):
    """Master index files store vol_driver on groups, not the top-level
    preset. Curated pairs must still clear gate_vol_driver or every
    indexes row lands as NONE."""
    _tickers, pairs, _description, driver = garch.resolve_inputs(None, "indexes")
    assert driver
    a, b = pairs[0]
    result = garch.analyze_pair(
        a,
        b,
        {
            a: garch.TickerVol(ticker=a, hv20=40, hv60=30, leap_atm_iv=20, iv_rank=20, has_leaps=True),
            b: garch.TickerVol(ticker=b, hv20=25, hv60=25, leap_atm_iv=30, iv_rank=20, has_leaps=True),
        },
        vol_driver=driver,
    )
    assert result.gate_vol_driver is True


def test_fetch_prices_ib_hit_skips_uw_ohlc(monkeypatch):
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
        garch,
        "_fetch_yahoo_prices",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("yahoo")),
    )

    prices = garch._fetch_prices("AAPL", uw, ib=RecordingIB())

    assert uw.calls == []
    assert len(prices) == 60
    assert prices[0] == 100.0
    assert prices[-1] == 159.0


def test_fetch_prices_ib_miss_calls_uw_ohlc(monkeypatch):
    from types import SimpleNamespace

    class RecordingUW:
        def __init__(self):
            self.calls = []

        def get_stock_ohlc(self, ticker, candle_size="1d"):
            self.calls.append((ticker, candle_size))
            return {"data": [{"close": float(i)} for i in range(1, 61)]}

    class RecordingIB:
        def get_historical_data(self, contract, **kwargs):
            return []

    uw = RecordingUW()
    monkeypatch.setattr(
        garch,
        "_fetch_yahoo_prices",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("yahoo")),
    )

    prices = garch._fetch_prices("NVDA", uw, ib=RecordingIB())

    assert uw.calls == [("NVDA", "1d")]
    assert prices == [float(i) for i in range(1, 61)]


class TestUwIvRankUnavailable:
    """R-199: a UW quota or auth failure must not read as the most bullish IV rank.

    ``_fetch_uw_iv`` swallowed ``UWAPIError`` — which both ``UWRateLimitError``
    and ``UWAuthError`` subclass — and returned a literal ``(0.0, 0.0)``. Zero
    is the *most* bullish value every downstream threshold tests for: gate 4
    wants ``< 50``, the STRONG tier wants ``< 30``, and a fabricated zero
    ``current_iv`` maximises the HV-IV gap too. A quota-exhausted run therefore
    manufactures STRONG long-vol trade ideas that are indistinguishable from a
    healthy scan.
    """

    def _client_raising(self, exc):
        class _Client:
            def get_iv_rank(_self, ticker):
                raise exc

        return _Client()

    def _vol(self, ticker, **kwargs):
        tv = garch.TickerVol(ticker=ticker)
        tv.hv20, tv.hv60, tv.hv252 = 60.0, 40.0, 45.0
        tv.leap_atm_iv = 20.0
        tv.leap_count, tv.has_leaps = 4, True
        for key, value in kwargs.items():
            setattr(tv, key, value)
        return tv

    @pytest.mark.parametrize("exc_name", ["UWRateLimitError", "UWAuthError"])
    def test_fetch_returns_a_sentinel_not_a_zero_rank(self, exc_name):
        from clients import uw_client as uw

        exc = getattr(uw, exc_name)("quota exhausted")
        iv, rank = garch._fetch_uw_iv("NVDA", self._client_raising(exc))
        assert rank is None, f"{exc_name} must not resolve to a real IV rank"
        assert iv is None

    def test_analyze_pair_refuses_a_pair_with_no_iv_rank(self):
        va = self._vol("NVDA", iv_rank=None, error="UW IV rank unavailable")
        vb = self._vol("AMD", iv_rank=35.0)
        pa = garch.analyze_pair("NVDA", "AMD", {"NVDA": va, "AMD": vb}, "AI capex")
        assert pa.signal == "NONE", (
            f"a pair missing an IV rank must not be emitted as {pa.signal}"
        )
        assert any("NVDA" in gate for gate in pa.failing_gates), pa.failing_gates

    def test_a_quota_failure_cannot_manufacture_a_strong_signal(self):
        # Divergence and HV gap both clear the STRONG thresholds; only the
        # fabricated iv_rank == 0.0 was standing between this pair and STRONG.
        leader = self._vol("NVDA", leap_atm_iv=60.0, iv_rank=45.0)
        lagger = self._vol("AMD", leap_atm_iv=10.0, iv_rank=None,
                           error="UW IV rank unavailable")
        pa = garch.analyze_pair("NVDA", "AMD", {"NVDA": leader, "AMD": lagger}, "AI capex")
        assert pa.signal != "STRONG"
        assert pa.lagger_iv_rank != 0.0

    def test_to_json_carries_the_ticker_error(self):
        payload = garch.to_json(
            {"NVDA": self._vol("NVDA", iv_rank=None, error="UW IV rank unavailable")},
            [],
        )
        entry = payload["tickers"]["NVDA"]
        assert entry["error"] == "UW IV rank unavailable"
        assert entry["iv_rank"] is None, (
            "a null rank must reach the tab as null, not as a plausible 0"
        )

    def test_healthy_fetch_is_unchanged(self):
        class _Client:
            def get_iv_rank(_self, ticker):
                return {"data": [{"volatility": 0.42, "iv_rank_1y": 31.5}]}

        iv, rank = garch._fetch_uw_iv("NVDA", _Client())
        assert rank == 31.5
        assert iv == pytest.approx(42.0)
