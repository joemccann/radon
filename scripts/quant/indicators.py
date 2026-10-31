"""Vectorised price indicators for the quant engine.

Every function takes a ``[T, N]`` matrix (a 1-D series is promoted to one
column) and returns a matrix of the same shape. Rolling windows need a FULL
window, else NaN (pandas ``rolling(w, min_periods=w)``); the rolling std is
the sample std (``ddof=1``) like pandas, so a ported strategy reproduces its
source numbers. numpy only.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

TRADING_DAYS_PER_YEAR = 252


def as_matrix(x) -> np.ndarray:
    arr = np.asarray(x, dtype=float)
    return arr if arr.ndim == 2 else arr.reshape(len(arr), -1)


def rolling_mean(x, window: int) -> np.ndarray:
    arr = as_matrix(x)
    if window <= 0:
        raise ValueError("window must be > 0")
    out = np.full(arr.shape, np.nan)
    if arr.shape[0] >= window:
        out[window - 1 :] = sliding_window_view(arr, window, axis=0).mean(axis=-1)
    return out


def rolling_std(x, window: int, ddof: int = 1) -> np.ndarray:
    arr = as_matrix(x)
    if window <= ddof:
        raise ValueError("window must exceed ddof")
    out = np.full(arr.shape, np.nan)
    if arr.shape[0] >= window:
        out[window - 1 :] = sliding_window_view(arr, window, axis=0).std(axis=-1, ddof=ddof)
    return out


sma = rolling_mean


def pct_change(x) -> np.ndarray:
    arr = as_matrix(x)
    out = np.full(arr.shape, np.nan)
    if arr.shape[0] > 1:
        out[1:] = arr[1:] / arr[:-1] - 1.0
    return out


def momentum(x, lookback: int) -> np.ndarray:
    """Return over ``lookback`` sessions: close[t] / close[t - lookback] - 1."""
    arr = as_matrix(x)
    if lookback <= 0:
        raise ValueError("lookback must be > 0")
    out = np.full(arr.shape, np.nan)
    if arr.shape[0] > lookback:
        out[lookback:] = arr[lookback:] / arr[:-lookback] - 1.0
    return out


def realized_vol(x, window: int) -> np.ndarray:
    """Annualised sample std of daily simple returns over ``window`` sessions."""
    return rolling_std(pct_change(x), window) * math.sqrt(TRADING_DAYS_PER_YEAR)
