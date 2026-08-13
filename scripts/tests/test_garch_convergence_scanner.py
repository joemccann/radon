"""Regression tests for garch_convergence.py explicit ticker-scan support."""
from __future__ import annotations

import garch_convergence as garch


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


def test_resolve_inputs_indexes_uses_curated_pairs_not_consecutive():
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


def test_resolve_inputs_indexes_passes_vol_driver_gate():
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
