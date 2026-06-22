"""Integration tests: risk_reversal + leap_iv_scanner use residual-vs-fitted.

These confirm the F9 wiring replaced (not duplicated) the raw point-IV path:
  - risk_reversal.build_matrix computes combo ``skew`` from SVI smile residuals,
  - leap_iv_scanner.analyze_mispricing scores from the smile residual and
    degrades to the HV-IV gap when the smile cannot be fit.

Fully offline: no IB connection is opened (build_matrix / analyze_mispricing
operate on plain dicts), so importing the modules does not touch the network.
"""
import numpy as np
import pytest

import risk_reversal
import leap_iv_scanner
from leap_iv_scanner import VolatilityData
from vol_surface import svi_total_variance, VolSurface


_BASE = dict(a=0.04, b=0.10, rho=-0.3, m=0.0, sigma=0.10)


def _synthetic_options(spot=100.0):
    """Build a risk_reversal-shaped options list from a known SVI smile.

    bullish: short = puts (below spot), long = calls (above spot).
    Each row carries the fields build_matrix consumes.
    """
    t = 45 / 365.0
    strikes = np.linspace(spot * 0.8, spot * 1.2, 25)
    k = np.log(strikes / spot)
    w = svi_total_variance(k, **_BASE)
    ivs = np.sqrt(w / t)

    options = []
    for K, iv in zip(strikes, ivs):
        # Crude monotone delta proxy so build_matrix's 0.25-0.50 filter has rows.
        moneyness = K / spot
        if K <= spot:  # put leg
            delta = -max(0.05, min(0.95, 0.5 - (spot - K) / spot * 2))
            right = "P"
        else:  # call leg
            delta = max(0.05, min(0.95, 0.5 - (K - spot) / spot * 2))
            right = "C"
        options.append({
            "expiry": "20260815",
            "dte": 45,
            "right": right,
            "strike": float(K),
            "bid": 1.0,
            "ask": 1.2,
            "mid": 1.1,
            "delta": float(delta),
            "gamma": 0.0,
            "theta": 0.0,
            "vega": 0.1,
            "iv": float(iv),
        })
    return options


def test_risk_reversal_skew_uses_smile_residual_not_raw_iv_diff():
    options = _synthetic_options()
    chain_data = {
        "spot": 100.0,
        "options": options,
        "short_right": "P",
        "long_right": "C",
        "expirations": ["20260815"],
    }
    matrix = risk_reversal.build_matrix(chain_data, bankroll=1_000_000, max_pct=0.025)

    assert matrix["all_combos"], "expected combos from synthetic chain"
    # On a perfectly on-smile chain the residual-based skew is ~0 for every
    # combo, whereas the OLD raw IV-diff would be large (puts vs calls IV gap).
    skews = [abs(c["skew"]) for c in matrix["all_combos"]]
    assert max(skews) < 1.0, f"residual skew should be ~0 on-smile, got max {max(skews)}"

    # Rich one put leg by 8 vol points -> its residual-based skew must jump.
    options_rich = _synthetic_options()
    put_rows = [o for o in options_rich if o["right"] == "P"]
    target = put_rows[len(put_rows) // 2]
    target["iv"] += 0.08
    chain_rich = dict(chain_data, options=options_rich)
    matrix_rich = risk_reversal.build_matrix(chain_rich, bankroll=1_000_000, max_pct=0.025)
    rich_combos = [c for c in matrix_rich["all_combos"] if abs(c["short_strike"] - target["strike"]) < 1e-6]
    assert rich_combos, "expected combos referencing the richened put strike"
    assert max(c["skew"] for c in rich_combos) > 3.0


def test_risk_reversal_falls_back_to_raw_iv_diff_when_unfittable():
    # Too few strikes -> surface unusable -> leg_skew falls back to raw IV diff.
    options = [
        {"expiry": "20260815", "dte": 45, "right": "P", "strike": 95.0, "bid": 1.0,
         "ask": 1.2, "mid": 1.1, "delta": -0.40, "gamma": 0, "theta": 0, "vega": 0.1, "iv": 0.30},
        {"expiry": "20260815", "dte": 45, "right": "C", "strike": 105.0, "bid": 1.0,
         "ask": 1.2, "mid": 1.1, "delta": 0.40, "gamma": 0, "theta": 0, "vega": 0.1, "iv": 0.20},
    ]
    chain_data = {"spot": 100.0, "options": options, "short_right": "P",
                  "long_right": "C", "expirations": ["20260815"]}
    matrix = risk_reversal.build_matrix(chain_data, bankroll=1_000_000, max_pct=0.025)
    assert matrix["all_combos"]
    # Raw IV diff = (0.30 - 0.20) * 100 = 10.0 points.
    assert matrix["all_combos"][0]["skew"] == pytest.approx(10.0)


def _leap_chain(spot=100.0, expiry="20271217"):
    from datetime import datetime
    dte = (datetime.strptime(expiry, "%Y%m%d") - datetime.now()).days
    t = dte / 365.0
    strikes = np.linspace(spot * 0.7, spot * 1.4, 25)
    k = np.log(strikes / spot)
    w = svi_total_variance(k, **_BASE)
    ivs = np.sqrt(w / t) * 100.0  # leap scanner stores IV in percent
    chain = []
    for K, iv in zip(strikes, ivs):
        chain.append({
            "strike": float(K), "expiry": expiry, "bid": 1.0, "ask": 1.2,
            "mid": 1.1, "iv": float(iv), "delta": 0.3, "vega": 0.30,
            "theta": -0.01, "oi": 0, "volume": 0,
        })
    return chain


def test_leap_analyze_mispricing_uses_smile_residual():
    chain = _leap_chain()
    surface = leap_iv_scanner.fit_chain_surface(chain, 100.0)
    assert surface.is_usable()

    vol = VolatilityData(ticker="TST", sector="X", current_price=100.0,
                         hv_20=40.0, hv_60=38.0, hv_252=35.0)

    # Pick a mid strike and mark it 20 vol points CHEAP vs the smile.
    opt = dict(chain[len(chain) // 2])
    opt["iv"] = opt["iv"] - 20.0
    analyzed = leap_iv_scanner.analyze_mispricing(opt, vol, min_gap=15.0, surface=surface)

    assert analyzed.smile_residual is not None
    assert analyzed.smile_residual < -15.0  # cheap vs smile
    assert analyzed.is_mispriced
    assert analyzed.mispricing_score > 0


def test_leap_on_smile_strike_is_not_flagged():
    chain = _leap_chain()
    surface = leap_iv_scanner.fit_chain_surface(chain, 100.0)
    vol = VolatilityData(ticker="TST", sector="X", current_price=100.0,
                         hv_20=40.0, hv_60=38.0, hv_252=35.0)
    opt = dict(chain[len(chain) // 2])  # exactly on the smile
    analyzed = leap_iv_scanner.analyze_mispricing(opt, vol, min_gap=15.0, surface=surface)
    assert abs(analyzed.smile_residual) < 1.0
    assert not analyzed.is_mispriced


def test_leap_falls_back_to_hv_gap_when_no_surface():
    vol = VolatilityData(ticker="TST", sector="X", current_price=100.0,
                         hv_20=45.0, hv_60=42.0, hv_252=38.0)
    opt = {"strike": 100.0, "expiry": "20271217", "bid": 1.0, "ask": 1.2,
           "mid": 1.1, "iv": 20.0, "delta": 0.3, "vega": 0.30, "theta": -0.01,
           "oi": 0, "volume": 0}
    analyzed = leap_iv_scanner.analyze_mispricing(opt, vol, min_gap=15.0, surface=None)
    # No surface -> residual is None and HV-IV gap drives the flag.
    assert analyzed.smile_residual is None
    assert analyzed.is_mispriced  # hv_20_gap = 45 - 20 = 25 >= 15
