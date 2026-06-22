"""Tests for the raw-SVI vol-surface fit (F9).

The SVI total-variance parameterization is

    w(k) = a + b * ( rho * (k - m) + sqrt((k - m)^2 + sigma^2) )

where k is log-moneyness and w = sigma_BS^2 * T is total implied variance.
A no-static-arbitrage (butterfly-free) slice satisfies Gatheral's constraints,
the strongest being b * (1 + |rho|) <= 4 / (1 + |rho|).

These tests fit synthetic, arbitrage-free smiles and assert:
  - residuals ~0 at the fitted sample points,
  - total variance is monotone increasing in T across calibrated slices
    (no calendar arbitrage),
  - the butterfly (static-arb) constraint is respected by the fit,
  - the surface degrades gracefully when too few strikes are supplied.

Pure numeric; no IB/UW/Turso/network.
"""
import math

import numpy as np
import pytest

from vol_surface import (
    SVIParams,
    VolSurface,
    fit_svi_slice,
    svi_total_variance,
    InsufficientStrikesError,
)


# ---------------------------------------------------------------------------
# Synthetic arbitrage-free smile generation
# ---------------------------------------------------------------------------

def _make_slice(a, b, rho, m, sigma, spot=100.0, t=0.25, n=21, strike_span=0.4):
    """Generate (strikes, ivs, spot, T) sampled from a known SVI slice.

    strike_span is the +/- fractional range around spot.
    """
    lo, hi = spot * (1 - strike_span), spot * (1 + strike_span)
    strikes = np.linspace(lo, hi, n)
    k = np.log(strikes / spot)
    w = svi_total_variance(k, a, b, rho, m, sigma)
    ivs = np.sqrt(w / t)
    return strikes, ivs, spot, t


# A calm, butterfly-free parameter set (b small enough for the constraint).
_BASE = dict(a=0.04, b=0.10, rho=-0.3, m=0.0, sigma=0.10)


# ---------------------------------------------------------------------------
# svi_total_variance closed form
# ---------------------------------------------------------------------------

def test_total_variance_matches_closed_form_at_the_money():
    # At k = m, the sqrt term is sigma, so w = a + b*sigma.
    w = svi_total_variance(np.array([0.0]), a=0.04, b=0.10, rho=-0.3, m=0.0, sigma=0.10)
    assert w[0] == pytest.approx(0.04 + 0.10 * 0.10, rel=1e-12)


def test_total_variance_is_positive_and_convex_in_k():
    k = np.linspace(-0.5, 0.5, 101)
    w = svi_total_variance(k, **_BASE)
    assert np.all(w > 0)
    # Convexity: second difference non-negative everywhere.
    second = np.diff(w, 2)
    assert np.all(second >= -1e-12)


# ---------------------------------------------------------------------------
# fit_svi_slice recovers the smile
# ---------------------------------------------------------------------------

def test_fit_recovers_arbitrage_free_slice_with_near_zero_residuals():
    strikes, ivs, spot, t = _make_slice(**_BASE)
    params = fit_svi_slice(strikes, ivs, spot, t)

    k = np.log(strikes / spot)
    w_fit = svi_total_variance(k, params.a, params.b, params.rho, params.m, params.sigma)
    w_true = svi_total_variance(k, **_BASE)

    rmse = math.sqrt(float(np.mean((w_fit - w_true) ** 2)))
    assert rmse < 1e-4


def test_fitted_vol_residual_near_zero_at_sample_points():
    strikes, ivs, spot, t = _make_slice(**_BASE)
    params = fit_svi_slice(strikes, ivs, spot, t)

    for K, iv in zip(strikes, ivs):
        resid = params.residual(K, iv)
        assert abs(resid) < 5e-3  # IV-point residual in vol units


# ---------------------------------------------------------------------------
# Butterfly (static / no-arb) constraint
# ---------------------------------------------------------------------------

def test_fit_respects_butterfly_constraint():
    strikes, ivs, spot, t = _make_slice(**_BASE)
    params = fit_svi_slice(strikes, ivs, spot, t)
    assert params.is_butterfly_arbitrage_free()


def test_fit_clamps_butterfly_violating_input():
    # Construct a slice with a huge b that violates Gatheral's bound, then add
    # noise. The calibrator must NOT return an arbitrageable slice.
    strikes, ivs, spot, t = _make_slice(a=0.04, b=2.5, rho=-0.2, m=0.0, sigma=0.05)
    rng = np.random.default_rng(7)
    ivs = ivs * (1 + rng.normal(0, 0.01, size=ivs.shape))
    params = fit_svi_slice(strikes, ivs, spot, t)
    assert params.is_butterfly_arbitrage_free()


# ---------------------------------------------------------------------------
# Calendar arbitrage: total variance monotone in T across the surface
# ---------------------------------------------------------------------------

def test_surface_total_variance_monotone_in_T():
    spot = 100.0
    # Three expiries with growing total variance (term structure rising).
    chain = {}
    for t, scale in [(0.10, 1.0), (0.30, 1.6), (0.80, 2.3)]:
        params = dict(_BASE)
        params["a"] = _BASE["a"] * scale
        params["b"] = _BASE["b"] * scale
        strikes, ivs, _, _ = _make_slice(spot=spot, t=t, **params)
        chain[t] = (strikes, ivs)

    surface = VolSurface.fit(chain, spot)

    ks = np.linspace(-0.2, 0.2, 9)
    expiries = sorted(chain.keys())
    for k in ks:
        ws = [surface.total_variance_at(k, t) for t in expiries]
        diffs = np.diff(ws)
        assert np.all(diffs >= -1e-8), f"calendar arb at k={k}: {ws}"


def test_surface_fitted_vol_and_residual_round_trip():
    spot = 100.0
    strikes, ivs, _, t = _make_slice(spot=spot, t=0.25, **_BASE)
    surface = VolSurface.fit({0.25: (strikes, ivs)}, spot)

    mid = strikes[len(strikes) // 2]
    fv = surface.fitted_vol(mid, 0.25)
    assert fv > 0
    # Residual of an on-smile point is ~0.
    iv_mid = ivs[len(ivs) // 2]
    assert abs(surface.residual(mid, 0.25, iv_mid)) < 5e-3


# ---------------------------------------------------------------------------
# Graceful degradation: too few strikes
# ---------------------------------------------------------------------------

def test_fit_too_few_strikes_raises():
    strikes = np.array([95.0, 100.0, 105.0])  # 3 < 5 SVI params
    ivs = np.array([0.22, 0.20, 0.21])
    with pytest.raises(InsufficientStrikesError):
        fit_svi_slice(strikes, ivs, spot=100.0, t=0.25)


def test_surface_skips_thin_slices_but_keeps_fittable_ones():
    spot = 100.0
    good_strikes, good_ivs, _, _ = _make_slice(spot=spot, t=0.25, **_BASE)
    thin_strikes = np.array([98.0, 100.0, 102.0])
    thin_ivs = np.array([0.21, 0.20, 0.21])

    surface = VolSurface.fit(
        {0.25: (good_strikes, good_ivs), 0.50: (thin_strikes, thin_ivs)},
        spot,
    )
    assert 0.25 in surface.slices
    assert 0.50 not in surface.slices
    # Querying an unfit expiry returns None rather than raising.
    assert surface.fitted_vol(100.0, 0.50) is None
    assert surface.residual(100.0, 0.50, 0.20) is None


def test_surface_fit_returns_empty_when_no_slice_fittable():
    spot = 100.0
    thin = (np.array([99.0, 100.0, 101.0]), np.array([0.2, 0.2, 0.2]))
    surface = VolSurface.fit({0.25: thin}, spot)
    assert surface.slices == {}
    assert not surface.is_usable()


# ---------------------------------------------------------------------------
# smile_residual helper (the shared chokepoint)
# ---------------------------------------------------------------------------

def test_smile_residual_zero_on_smile_and_signs_off_smile():
    from vol_surface import smile_residual

    strikes, ivs, spot, t = _make_slice(**_BASE)
    surface = VolSurface.fit({t: (strikes, ivs)}, spot)
    dte = round(t * 365.0)

    mid = strikes[len(strikes) // 2]
    iv_mid = ivs[len(ivs) // 2]
    assert abs(smile_residual(surface, mid, dte, iv_mid)) < 5e-3

    # Trade the same strike 5 vol points rich -> positive residual.
    assert smile_residual(surface, mid, dte, iv_mid + 0.05) > 0.04
    # And 5 vol points cheap -> negative.
    assert smile_residual(surface, mid, dte, iv_mid - 0.05) < -0.04


def test_smile_residual_none_when_surface_unusable():
    from vol_surface import smile_residual

    thin = (np.array([99.0, 100.0, 101.0]), np.array([0.2, 0.2, 0.2]))
    surface = VolSurface.fit({0.25: thin}, 100.0)
    assert smile_residual(surface, 100.0, 90, 0.2) is None
    assert smile_residual(None, 100.0, 90, 0.2) is None
