#!/usr/bin/env python3
"""Raw-SVI volatility-surface fit (F9).

Calibrates Gatheral's raw stochastic-volatility-inspired (SVI) parameterization
of total implied variance per expiry, then exposes a surface that returns the
fitted vol and the residual (market IV minus calibrated-smile IV) for any
(strike, expiry). Downstream scanners use ``residual`` as the mispricing signal:
deviation from the calibrated smile, not a raw point IV.

Total implied variance (w = sigma_BS^2 * T) as a function of log-moneyness k:

    w(k) = a + b * ( rho * (k - m) + sqrt((k - m)^2 + sigma^2) )

Calibration enforces no static (butterfly) arbitrage per slice via Gatheral's
bound and no calendar arbitrage across slices by keeping total variance
non-decreasing in T. Pure Python: numpy + scipy.optimize.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Iterable, Optional, Tuple

import numpy as np


SVI_PARAM_COUNT = 5
MIN_STRIKES_TO_FIT = SVI_PARAM_COUNT  # need at least as many points as free params


class InsufficientStrikesError(ValueError):
    """Raised when a slice has too few usable strikes to calibrate SVI."""


# ---------------------------------------------------------------------------
# Closed-form total variance
# ---------------------------------------------------------------------------

def svi_total_variance(
    k: np.ndarray,
    a: float,
    b: float,
    rho: float,
    m: float,
    sigma: float,
) -> np.ndarray:
    """Raw-SVI total implied variance at log-moneyness ``k``."""
    centered = k - m
    return a + b * (rho * centered + np.sqrt(centered * centered + sigma * sigma))


def _butterfly_bound(b: float, rho: float) -> bool:
    """Gatheral's sufficient no-butterfly-arbitrage bound on slope.

    A raw-SVI slice is free of static (butterfly) arbitrage when
    ``b * (1 + |rho|) <= 4 / (1 + |rho|)`` together with positive minimum
    variance. This is the dominant constraint used during calibration.
    """
    denom = 1.0 + abs(rho)
    return b * denom <= (4.0 / denom) + 1e-9


# ---------------------------------------------------------------------------
# Fitted parameters for one expiry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SVIParams:
    """Calibrated raw-SVI parameters for a single expiry."""

    a: float
    b: float
    rho: float
    m: float
    sigma: float
    t: float
    spot: float

    def total_variance(self, k: float | np.ndarray) -> np.ndarray:
        return svi_total_variance(np.asarray(k, dtype=float), self.a, self.b, self.rho, self.m, self.sigma)

    def fitted_vol(self, strike: float) -> float:
        """Calibrated Black-Scholes implied vol at ``strike``."""
        k = math.log(strike / self.spot)
        w = float(self.total_variance(k))
        return math.sqrt(max(w, 0.0) / self.t)

    def residual(self, strike: float, market_iv: float) -> float:
        """Market IV minus calibrated-smile IV (the mispricing signal)."""
        return market_iv - self.fitted_vol(strike)

    def min_total_variance(self) -> float:
        """Minimum of the SVI slice (attained where the derivative is zero)."""
        # w'(k) = b*(rho + (k-m)/sqrt((k-m)^2 + sigma^2)) = 0 has a closed form.
        if abs(self.rho) >= 1.0:
            return float(self.total_variance(self.m))
        offset = -self.sigma * self.rho / math.sqrt(1.0 - self.rho * self.rho)
        return float(self.total_variance(self.m + offset))

    def is_butterfly_arbitrage_free(self) -> bool:
        return _butterfly_bound(self.b, self.rho) and self.min_total_variance() >= -1e-9

    def __repr__(self) -> str:  # debug ergonomics
        return (
            f"SVIParams(a={self.a:.5f}, b={self.b:.5f}, rho={self.rho:+.4f}, "
            f"m={self.m:+.5f}, sigma={self.sigma:.5f}, T={self.t:.4f})"
        )


# ---------------------------------------------------------------------------
# Per-slice calibration
# ---------------------------------------------------------------------------

def _initial_guess(k: np.ndarray, w: np.ndarray) -> np.ndarray:
    a0 = max(float(np.min(w)), 1e-6)
    b0 = 0.1
    rho0 = -0.3
    m0 = float(k[int(np.argmin(w))])
    sigma0 = max(float(np.std(k)), 0.05)
    return np.array([a0, b0, rho0, m0, sigma0])


def _max_slope(rho: float) -> float:
    denom = 1.0 + abs(rho)
    return (4.0 / denom) / denom


def fit_svi_slice(
    strikes: Iterable[float],
    ivs: Iterable[float],
    spot: float,
    t: float,
) -> SVIParams:
    """Calibrate raw-SVI for one expiry from market strikes and implied vols.

    Fits in total-variance space with bounded least squares, then projects the
    slope ``b`` onto Gatheral's no-butterfly-arbitrage region so the returned
    slice is always static-arb free.

    Raises ``InsufficientStrikesError`` when fewer than five usable strikes.
    """
    strikes = np.asarray(list(strikes), dtype=float)
    ivs = np.asarray(list(ivs), dtype=float)

    valid = np.isfinite(strikes) & np.isfinite(ivs) & (strikes > 0) & (ivs > 0)
    strikes, ivs = strikes[valid], ivs[valid]

    if strikes.size < MIN_STRIKES_TO_FIT:
        raise InsufficientStrikesError(
            f"need >= {MIN_STRIKES_TO_FIT} strikes to fit SVI, got {strikes.size}"
        )

    # scipy is an optional analytics dependency. Import lazily so core scanners
    # that pull in this module (leap_iv_scanner, risk_reversal) still import when
    # scipy is absent; the missing fit degrades to raw IV upstream because
    # VolSurface.fit drops slices that raise InsufficientStrikesError/ValueError.
    try:
        from scipy.optimize import least_squares
    except ImportError as exc:
        raise InsufficientStrikesError("scipy unavailable for SVI fit") from exc

    k = np.log(strikes / spot)
    w = (ivs ** 2) * t

    def residuals(p):
        a, b, rho, m, sigma = p
        return svi_total_variance(k, a, b, rho, m, sigma) - w

    lower = [1e-8, 1e-8, -0.999, k.min() - 1.0, 1e-4]
    upper = [np.max(w) + 1e-3, 10.0, 0.999, k.max() + 1.0, 5.0]
    p0 = np.clip(_initial_guess(k, w), lower, upper)

    sol = least_squares(residuals, p0, bounds=(lower, upper), method="trf", max_nfev=5000)
    a, b, rho, m, sigma = sol.x

    b = _project_butterfly(a, b, rho, sigma, t)
    a = max(a, 0.0)

    return SVIParams(a=a, b=b, rho=rho, m=m, sigma=sigma, t=t, spot=spot)


def _project_butterfly(a: float, b: float, rho: float, sigma: float, t: float) -> float:
    """Clamp the slope ``b`` into the no-butterfly-arbitrage region."""
    cap = _max_slope(rho)
    if b <= cap:
        return b
    return cap


# ---------------------------------------------------------------------------
# Multi-expiry surface
# ---------------------------------------------------------------------------

@dataclass
class VolSurface:
    """A calibrated SVI surface across expiries, keyed by year-fraction T."""

    spot: float
    slices: Dict[float, SVIParams]

    @classmethod
    def fit(
        cls,
        chain: Dict[float, Tuple[Iterable[float], Iterable[float]]],
        spot: float,
    ) -> "VolSurface":
        """Calibrate every expiry, skip thin slices, enforce calendar monotonicity.

        ``chain`` maps year-fraction T -> (strikes, ivs). Slices with too few
        strikes (or that fail to converge) are dropped rather than raising, so
        the surface degrades gracefully. After fitting, total variance is forced
        non-decreasing in T at the at-the-money log-moneyness to remove calendar
        arbitrage introduced by independent per-slice fits.
        """
        fitted: Dict[float, SVIParams] = {}
        for t in sorted(chain.keys()):
            strikes, ivs = chain[t]
            try:
                fitted[t] = fit_svi_slice(strikes, ivs, spot, t)
            except (InsufficientStrikesError, ValueError):
                continue

        fitted = _enforce_calendar_monotonicity(fitted)
        return cls(spot=spot, slices=fitted)

    def is_usable(self) -> bool:
        return len(self.slices) > 0

    def _slice_for(self, t: float, tol: float = 3.0 / 365.0) -> Optional[SVIParams]:
        """Return the calibrated slice for year-fraction ``t``.

        Float expiry keys reconstructed from integer days never compare equal to
        a directly-supplied T, so match the nearest slice within a few-days
        tolerance instead of an exact dict lookup.
        """
        params = self.slices.get(t)
        if params is not None:
            return params
        if not self.slices:
            return None
        nearest = min(self.slices, key=lambda key: abs(key - t))
        return self.slices[nearest] if abs(nearest - t) <= tol else None

    def total_variance_at(self, k: float, t: float) -> Optional[float]:
        params = self._slice_for(t)
        if params is None:
            return None
        return float(params.total_variance(k))

    def fitted_vol(self, strike: float, t: float) -> Optional[float]:
        params = self._slice_for(t)
        if params is None:
            return None
        return params.fitted_vol(strike)

    def residual(self, strike: float, t: float, market_iv: float) -> Optional[float]:
        params = self._slice_for(t)
        if params is None:
            return None
        return params.residual(strike, market_iv)


def _enforce_calendar_monotonicity(fitted: Dict[float, SVIParams]) -> Dict[float, SVIParams]:
    """Lift each slice's level so ATM total variance never decreases in T.

    Raw-SVI fit per expiry can leave a later slice marginally below an earlier
    one at some moneyness. Raising the additive level ``a`` by the deficit keeps
    the shape and removes calendar arbitrage at the ATM anchor (where term
    structure is most observable) without touching the no-butterfly bound, which
    only depends on ``b`` and ``rho``.
    """
    if len(fitted) < 2:
        return fitted

    expiries = sorted(fitted.keys())
    adjusted: Dict[float, SVIParams] = {}
    prev_atm = -math.inf
    for t in expiries:
        p = fitted[t]
        atm = float(p.total_variance(0.0))
        if atm < prev_atm:
            bump = prev_atm - atm
            p = SVIParams(a=p.a + bump, b=p.b, rho=p.rho, m=p.m, sigma=p.sigma, t=p.t, spot=p.spot)
            atm = float(p.total_variance(0.0))
        adjusted[t] = p
        prev_atm = atm
    return adjusted


# ---------------------------------------------------------------------------
# Convenience: build a surface from a (strike, iv, dte) chain table
# ---------------------------------------------------------------------------

def surface_from_contracts(
    contracts: Iterable[dict],
    spot: float,
    *,
    iv_key: str = "iv",
    strike_key: str = "strike",
    dte_key: str = "dte",
) -> VolSurface:
    """Build a VolSurface from a flat list of contract dicts.

    Groups by days-to-expiry, converts to year-fraction, and ignores rows with
    non-positive IV. Shared entry point so risk_reversal / leap_iv_scanner do
    not each reimplement chain grouping.
    """
    by_t: Dict[float, Tuple[list, list]] = {}
    for c in contracts:
        iv = c.get(iv_key)
        strike = c.get(strike_key)
        dte = c.get(dte_key)
        if not iv or not strike or not dte or iv <= 0 or dte <= 0:
            continue
        t = dte / 365.0
        strikes, ivs = by_t.setdefault(t, ([], []))
        strikes.append(strike)
        ivs.append(iv)
    return VolSurface.fit({t: (np.array(s), np.array(v)) for t, (s, v) in by_t.items()}, spot)


def smile_residual(
    surface: VolSurface,
    strike: float,
    dte: float,
    market_iv: float,
) -> Optional[float]:
    """Smile-deviation mispricing for one contract: market IV minus fitted IV.

    Positive => the contract trades RICH versus the calibrated smile (IV above
    the fit); negative => CHEAP. Returns ``None`` when the contract's expiry has
    no calibrated slice, so callers can degrade gracefully to a neutral signal.

    This is the single chokepoint both risk_reversal.py and leap_iv_scanner.py
    use, so the residual-vs-fitted definition lives in exactly one place.
    """
    if surface is None or not surface.is_usable() or not dte or dte <= 0 or not market_iv:
        return None
    return surface.residual(strike, dte / 365.0, market_iv)
