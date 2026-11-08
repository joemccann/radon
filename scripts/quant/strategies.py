"""Strategy registry: the quants.md / QuantJourney rules ported one to one.

Each entry names its source file so the rule can be diffed against the
original. A strategy maps a ``[T, N]`` close matrix to a ``[T, N]`` target
weight matrix; rows before ``warmup`` are flat. Nothing here reads flow,
options or the gates: this is the systematic sleeve (docs/quant-strategies.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

import numpy as np

from .engine import RebalancePolicy, apply_vol_target
from .indicators import as_matrix, momentum, realized_vol, rolling_mean

UNIVERSE_MULTI_ASSET: tuple[str, ...] = ("SPY", "EFA", "EEM", "TLT", "IEF", "GLD", "DBC", "VNQ")
#: ETF stand-ins for W25's MES, MNQ, ZN, CL, GC, ZC continuous futures.
UNIVERSE_FUTURES_PROXY: tuple[str, ...] = ("SPY", "QQQ", "IEF", "USO", "GLD", "CORN")

WeightsFn = Callable[[np.ndarray, Mapping[str, Any]], np.ndarray]
OverlayFn = Callable[[np.ndarray, np.ndarray, Optional[Sequence[str]]], np.ndarray]


@dataclass(frozen=True)
class QuantStrategy:
    key: str
    label: str
    source: str
    universe: tuple[str, ...]
    policy: RebalancePolicy
    warmup: int
    long_short: bool
    params: dict[str, Any]
    weights_fn: WeightsFn
    overlay: Optional[OverlayFn] = None
    max_gross: float = 1.0
    default_since: str = "2007-01-03"
    notes: str = ""

    def target_weights(self, closes, dates: Optional[Sequence[str]] = None) -> np.ndarray:
        px = as_matrix(closes)
        if px.shape[1] != len(self.universe):
            raise ValueError(f"{self.key}: expected {len(self.universe)} columns, got {px.shape[1]}")
        weights = self.weights_fn(px, self.params)
        if self.overlay is not None:
            weights = self.overlay(weights, px, dates)
        weights = np.nan_to_num(np.asarray(weights, dtype=float), nan=0.0)
        weights[: self.warmup] = 0.0
        return weights

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "source": self.source,
            "universe": list(self.universe),
            "policy": self.policy.to_dict(),
            "warmup": self.warmup,
            "long_short": self.long_short,
            "params": dict(self.params),
            "max_gross": self.max_gross,
            "default_since": self.default_since,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# rules                                                                        #
# --------------------------------------------------------------------------- #
def _row_normalise(raw: np.ndarray) -> np.ndarray:
    """Divide each row by its gross so |row| sums to one; flat rows stay flat."""
    gross = np.abs(raw).sum(axis=1, keepdims=True)
    return np.divide(raw, gross, out=np.zeros_like(raw), where=gross > 0)


def trend_inverse_vol(closes: np.ndarray, p: Mapping[str, Any]) -> np.ndarray:
    """W25: sign(SMA fast - SMA slow) / vol, unit gross, long/short."""
    fast = rolling_mean(closes, p["fast"])
    slow = rolling_mean(closes, p["slow"])
    valid = np.isfinite(fast) & np.isfinite(slow)
    signal = np.where(valid, np.sign(fast - slow), 0.0)
    vol = realized_vol(closes, p["vol_window"])
    vol = np.where(vol > 0, vol, np.nan)
    raw = np.nan_to_num(signal / vol, nan=0.0, posinf=0.0, neginf=0.0)
    return _row_normalise(raw)


def inverse_vol(closes: np.ndarray, p: Mapping[str, Any]) -> np.ndarray:
    """W05: long only, weight proportional to 1 / realised vol, per-name cap."""
    vol = realized_vol(closes, p["lookback"])
    inv = np.where(vol > 0, 1.0 / vol, np.nan)
    total = np.nansum(inv, axis=1, keepdims=True)
    weights = np.nan_to_num(np.divide(inv, total, out=np.full_like(inv, np.nan), where=total > 0), nan=0.0)
    return np.minimum(weights, p["cap"])


def top_n_momentum(closes: np.ndarray, p: Mapping[str, Any]) -> np.ndarray:
    """O17 / W04: equal weight across the top ``top_n`` names by momentum.

    ``require_positive`` (W04's absolute-momentum filter) drops names whose
    momentum is not above zero; an empty pick list is cash.
    """
    mom = momentum(closes, p["lookback"])
    weights = np.zeros_like(closes)
    top_n = int(p["top_n"])
    for i in range(mom.shape[0]):
        row = mom[i]
        eligible = np.flatnonzero(np.isfinite(row) & ((row > 0) if p.get("require_positive") else True))
        if eligible.size == 0:
            continue
        picks = eligible[np.argsort(-row[eligible], kind="stable")[:top_n]]
        weights[i, picks] = 1.0 / picks.size
    return np.minimum(weights, p["cap"])


def sma_long_cash(closes: np.ndarray, p: Mapping[str, Any]) -> np.ndarray:
    """W17 base: hold each name while SMA fast > SMA slow, equal weight, cap."""
    fast = rolling_mean(closes, p["fast"])
    slow = rolling_mean(closes, p["slow"])
    active = np.isfinite(fast) & np.isfinite(slow) & (fast > slow)
    counts = active.sum(axis=1, keepdims=True)
    weights = np.divide(active.astype(float), counts, out=np.zeros_like(closes), where=counts > 0)
    return np.minimum(weights, p["cap"])


def defensive_rotation(closes: np.ndarray, p: Mapping[str, Any]) -> np.ndarray:
    """W06: SPY above its SMA -> equal-weight risk-on basket, else defensive."""
    spy = closes[:, p["spy_index"]]
    sma = rolling_mean(spy, p["window"])[:, 0]
    valid = np.isfinite(sma)
    risk_on = valid & (spy > sma)
    risk_off = valid & ~risk_on
    weights = np.zeros_like(closes)
    on_idx, off_idx = list(p["risk_on_index"]), list(p["defensive_index"])
    weights[np.ix_(risk_on, on_idx)] = 1.0 / len(on_idx)
    weights[np.ix_(risk_off, off_idx)] = 1.0 / len(off_idx)
    return np.minimum(weights, p["cap"])


def _vol_target_overlay(target_vol: float, lookback: int, max_leverage: float) -> OverlayFn:
    def overlay(weights: np.ndarray, closes: np.ndarray, dates: Optional[Sequence[str]]) -> np.ndarray:
        return apply_vol_target(
            weights, closes, dates, target_vol=target_vol, lookback=lookback, max_leverage=max_leverage
        )

    return overlay


# --------------------------------------------------------------------------- #
# registry                                                                     #
# --------------------------------------------------------------------------- #
_MA = UNIVERSE_MULTI_ASSET

STRATEGIES: dict[str, QuantStrategy] = {
    "trend_inverse_vol": QuantStrategy(
        key="trend_inverse_vol",
        label="Trend, inverse vol (futures proxy)",
        source="quants.md W25 / example_weights_25_continuous_futures_trend.py",
        universe=UNIVERSE_FUTURES_PROXY,
        policy=RebalancePolicy("BME"),
        warmup=252,
        long_short=True,
        params={"fast": 63, "slow": 252, "vol_window": 63},
        weights_fn=trend_inverse_vol,
        default_since="2010-06-09",
        notes="ETF proxies for MES, MNQ, ZN, CL, GC, ZC; the source ran provider continuous futures.",
    ),
    "inverse_vol": QuantStrategy(
        key="inverse_vol",
        label="Monthly inverse volatility",
        source="quants.md W05 / example_weights_05_monthly_inverse_vol.py",
        universe=_MA,
        policy=RebalancePolicy("BME"),
        warmup=63,
        long_short=False,
        params={"lookback": 63, "cap": 0.35},
        weights_fn=inverse_vol,
    ),
    "monthly_rotation": QuantStrategy(
        key="monthly_rotation",
        label="Monthly momentum rotation",
        source="quants.md O17 / example_orders_17_monthly_rotation_orders.py",
        universe=_MA,
        policy=RebalancePolicy("BMS"),
        warmup=126,
        long_short=False,
        params={"lookback": 126, "top_n": 3, "cap": 0.40, "require_positive": False},
        weights_fn=top_n_momentum,
    ),
    "dual_momentum": QuantStrategy(
        key="dual_momentum",
        label="Quarterly dual momentum",
        source="quants.md W04 / example_weights_04_quarterly_dual_momentum.py",
        universe=_MA,
        policy=RebalancePolicy("BQE"),
        warmup=252,
        long_short=False,
        params={"lookback": 252, "top_n": 2, "cap": 0.50, "require_positive": True},
        weights_fn=top_n_momentum,
    ),
    "vol_target_trend": QuantStrategy(
        key="vol_target_trend",
        label="Vol-targeted trend",
        source="quants.md W17 / example_weights_17_vol_target_trend.py",
        universe=_MA,
        policy=RebalancePolicy("BME"),
        warmup=200,
        long_short=False,
        params={"fast": 50, "slow": 200, "cap": 0.34, "target_vol": 0.10, "vol_lookback": 63, "max_leverage": 1.5},
        weights_fn=sma_long_cash,
        overlay=_vol_target_overlay(0.10, 63, 1.5),
        max_gross=1.5,
        notes="The source levers to 1.5x in calm regimes; pass --max-gross 1.0 to forbid leverage.",
    ),
    "signal_change_defensive": QuantStrategy(
        key="signal_change_defensive",
        label="Signal-change defensive rotation",
        source="quants.md W06 / example_weights_06_signal_change_defensive.py",
        universe=_MA,
        policy=RebalancePolicy("SIGNAL", signal_change_threshold=0.01),
        warmup=200,
        long_short=False,
        params={
            "window": 200,
            "cap": 0.50,
            "spy_index": _MA.index("SPY"),
            "risk_on_index": tuple(_MA.index(s) for s in ("SPY", "EFA", "EEM", "DBC", "VNQ")),
            "defensive_index": tuple(_MA.index(s) for s in ("TLT", "IEF", "GLD")),
        },
        weights_fn=defensive_rotation,
    ),
}


def get_strategy(key: str) -> QuantStrategy:
    try:
        return STRATEGIES[key]
    except KeyError:
        raise KeyError(f"unknown strategy: {key}") from None


def list_strategies() -> list[dict[str, Any]]:
    return [s.to_dict() for s in STRATEGIES.values()]
