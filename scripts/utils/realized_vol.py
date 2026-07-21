"""Shared realized-volatility math.

Extracted verbatim from scripts/cri_scan.py (rv-ratio plan, section 3.2)
so cri_scan and rv_ratio_scan compute the same quantity from one body of
code: close-to-close log returns, sample std (ddof=1), annualized by
sqrt(252), expressed in percentage points.

``compute_realized_vol`` is the contract; ``compute_realized_vol_series``
is the vectorized rolling variant, parity-pinned against the scalar at
every index by scripts/tests/test_realized_vol.py.
"""
from __future__ import annotations

import numpy as np

ANNUALIZATION_SESSIONS = 252


def compute_realized_vol(prices: np.ndarray, window: int) -> float:
    """Compute annualized realized volatility from the trailing window.

    Returns vol in percentage points (e.g. 25.0 for 25%); NaN when fewer
    than ``window + 1`` prices are available.
    """
    if len(prices) < window + 1:
        return float("nan")
    log_returns = np.log(prices[-window:] / prices[-window - 1:-1])
    return float(np.std(log_returns, ddof=1) * np.sqrt(ANNUALIZATION_SESSIONS) * 100)


def compute_realized_vol_series(closes: np.ndarray, window: int) -> np.ndarray:
    """Rolling realized vol aligned to ``closes``: ``series[t]`` equals
    ``compute_realized_vol(closes[:t + 1], window)``.

    Entries before index ``window`` are NaN (not enough returns yet).
    """
    closes = np.asarray(closes, dtype=float)
    series = np.full(len(closes), np.nan)
    if len(closes) < window + 1:
        return series
    log_returns = np.log(closes[1:] / closes[:-1])
    windows = np.lib.stride_tricks.sliding_window_view(log_returns, window)
    series[window:] = np.std(windows, ddof=1, axis=-1) * np.sqrt(ANNUALIZATION_SESSIONS) * 100
    return series
