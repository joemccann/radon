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
