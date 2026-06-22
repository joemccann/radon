"""Tests for the F5 Polymarket event-odds overlay (scripts/fetch_event_odds.py).

The fetcher:
  1. maps a relevant Polymarket market to a ticker/catalyst (macro/index mapping,
     e.g. an FOMC market -> SPX),
  2. converts the Polymarket binary midpoint to an implied probability,
  3. derives an options-skew-implied probability from fetch_options.py for the
     same ticker/event window,
  4. computes the divergence (polymarket_prob - options_prob).

Everything external is mocked: the Polymarket client is a MagicMock and
fetch_options is monkeypatched. Nothing touches the network or the DB.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

import fetch_event_odds as feo


FIXED_NOW = datetime(2026, 6, 21, 12, 0, tzinfo=timezone.utc)


# ── market -> ticker mapping ───────────────────────────────────────

def test_map_market_to_ticker_fomc_maps_to_spx():
    assert feo.map_market_to_ticker("Will the Fed cut rates at the June FOMC?") == "SPX"


def test_map_market_to_ticker_cpi_maps_to_spx():
    assert feo.map_market_to_ticker("Will CPI inflation exceed 3% in June?") == "SPX"


def test_map_market_to_ticker_unmapped_returns_none():
    assert feo.map_market_to_ticker("Will Team X win the championship?") is None


def test_map_market_is_case_insensitive():
    assert feo.map_market_to_ticker("WILL THE FOMC HIKE?") == "SPX"


# ── options-skew implied probability ───────────────────────────────

def test_options_skew_probability_bullish_chain_above_half():
    options = {"analysis": {"combined_bias": "BULLISH"},
               "chain": {"put_call_ratio": 0.4}}
    prob = feo.options_skew_probability(options)
    assert prob is not None
    assert prob > 0.5


def test_options_skew_probability_bearish_chain_below_half():
    options = {"analysis": {"combined_bias": "BEARISH"},
               "chain": {"put_call_ratio": 3.0}}
    prob = feo.options_skew_probability(options)
    assert prob is not None
    assert prob < 0.5


def test_options_skew_probability_neutral_near_half():
    options = {"analysis": {"combined_bias": "NEUTRAL"},
               "chain": {"put_call_ratio": 1.0}}
    prob = feo.options_skew_probability(options)
    assert prob == pytest.approx(0.5, abs=0.05)


def test_options_skew_probability_none_without_chain():
    assert feo.options_skew_probability({"analysis": {}, "chain": None}) is None


def test_options_skew_probability_stays_in_unit_interval():
    options = {"analysis": {"combined_bias": "BULLISH"},
               "chain": {"put_call_ratio": 0.01}}
    prob = feo.options_skew_probability(options)
    assert 0.0 <= prob <= 1.0


# ── divergence math ────────────────────────────────────────────────

def test_compute_divergence_subtracts_options_from_polymarket():
    div = feo.compute_divergence(polymarket_prob=0.70, options_prob=0.55)
    assert div == pytest.approx(0.15)


def test_compute_divergence_none_when_either_missing():
    assert feo.compute_divergence(0.7, None) is None
    assert feo.compute_divergence(None, 0.5) is None


# ── end-to-end overlay (mocked client + fetch_options) ─────────────

def _make_client() -> MagicMock:
    client = MagicMock()
    client.get_markets.return_value = [
        {
            "question": "Will the Fed cut rates at the June FOMC?",
            "active": True,
            "clobTokenIds": ["0xYES", "0xNO"],
            "endDate": "2026-06-30T00:00:00Z",
        },
        {
            "question": "Will Team X win the championship?",
            "active": True,
            "clobTokenIds": ["0xA", "0xB"],
        },
    ]
    client.get_midpoint.return_value = 0.62
    return client


def test_fetch_event_odds_maps_and_computes_divergence(monkeypatch):
    client = _make_client()

    def _fake_fetch_options(ticker, **kwargs):
        assert ticker == "SPX"
        return {"ticker": "SPX",
                "analysis": {"combined_bias": "NEUTRAL"},
                "chain": {"put_call_ratio": 1.0}}

    monkeypatch.setattr(feo, "fetch_options", _fake_fetch_options)

    payload = feo.fetch_event_odds("SPX", client=client, now=FIXED_NOW)

    assert payload["ticker"] == "SPX"
    overlays = payload["overlays"]
    assert len(overlays) == 1, "only the FOMC market maps to SPX"
    overlay = overlays[0]
    assert overlay["polymarket_prob"] == pytest.approx(0.62)
    assert overlay["options_prob"] is not None
    assert overlay["divergence"] == pytest.approx(
        overlay["polymarket_prob"] - overlay["options_prob"]
    )
    assert "FOMC" in overlay["question"] or "Fed" in overlay["question"]


def test_fetch_event_odds_empty_when_no_market_maps(monkeypatch):
    client = MagicMock()
    client.get_markets.return_value = [
        {"question": "Will Team X win?", "active": True, "clobTokenIds": ["0xA", "0xB"]},
    ]
    monkeypatch.setattr(feo, "fetch_options", lambda *a, **k: {"chain": None, "analysis": {}})

    payload = feo.fetch_event_odds("SPX", client=client, now=FIXED_NOW)
    assert payload["overlays"] == []


def test_fetch_event_odds_tolerates_options_failure(monkeypatch):
    client = _make_client()

    def _boom(ticker, **kwargs):
        raise RuntimeError("IB/UW down")

    monkeypatch.setattr(feo, "fetch_options", _boom)

    payload = feo.fetch_event_odds("SPX", client=client, now=FIXED_NOW)
    # Market still mapped + polymarket prob present; options side is None.
    assert len(payload["overlays"]) == 1
    overlay = payload["overlays"][0]
    assert overlay["polymarket_prob"] == pytest.approx(0.62)
    assert overlay["options_prob"] is None
    assert overlay["divergence"] is None


def test_has_overlay_activity_gate():
    assert feo.has_overlay_activity({"overlays": [{"polymarket_prob": 0.5}]}) is True
    assert feo.has_overlay_activity({"overlays": []}) is False


def test_run_caches_only_when_active(monkeypatch, tmp_path):
    client = _make_client()
    monkeypatch.setattr(
        feo, "fetch_options",
        lambda *a, **k: {"analysis": {"combined_bias": "NEUTRAL"}, "chain": {"put_call_ratio": 1.0}},
    )
    monkeypatch.setattr(feo, "DATA_DIR", tmp_path)
    writes = {}
    monkeypatch.setattr(feo, "_write_db_cache", lambda p, s: writes.setdefault("called", True))

    result = feo.run("SPX", client=client, now=FIXED_NOW)
    assert writes.get("called") is True
    assert (tmp_path / "event_odds" / "SPX.json").exists()
    assert result["ticker"] == "SPX"


def test_run_does_not_cache_empty(monkeypatch, tmp_path):
    client = MagicMock()
    client.get_markets.return_value = []
    monkeypatch.setattr(feo, "fetch_options", lambda *a, **k: {"chain": None, "analysis": {}})
    monkeypatch.setattr(feo, "DATA_DIR", tmp_path)
    writes = {}
    monkeypatch.setattr(feo, "_write_db_cache", lambda p, s: writes.setdefault("called", True))

    feo.run("SPX", client=client, now=FIXED_NOW)
    assert "called" not in writes
    assert not (tmp_path / "event_odds" / "SPX.json").exists()
