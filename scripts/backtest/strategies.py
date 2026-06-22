"""Strategy registry for the backtester.

Each strategy is one entry in ``STRATEGIES`` describing:
  - ``key``        — URL / CLI identifier
  - ``label``      — human name (matches docs/strategies.md)
  - ``wired``      — True iff the entry rule + signal loader are implemented
  - ``entry_doc``  — the documented entry rule, lifted from docs/strategies.md
  - ``loader``     — () -> (points, underlying)  (only for wired strategies)
  - ``rule``       — EntryRule  (only for wired strategies)

Be honest in code about which strategies are wired: ``run_strategy`` raises a
clear ``NotImplementedError`` for the stubbed ones rather than silently
returning an empty/fake result.

Only **Crash Risk Index (CRI)** is fully wired in this first increment. Its
snapshot history is the one source that already carries a clean daily series
with an aligned underlying close (SPY), which is exactly what a look-ahead-free
mark-to-market needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from .engine import SignalPoint, walk_forward_backtest
from .signal_replay import forward_returns_from_underlying, load_cri_series

# --- documented CRI entry rule (docs/strategies.md Strategy 6) -------------- #
#: Crash Trigger fires when ALL three convergence conditions hold. When it
#: fires the strategy buys SPY puts — modelled here as a SHORT SPY position so a
#: drop in SPY produces a positive return for the hedge.
CRI_REALIZED_VOL_THRESHOLD: float = 25.0  # 20d realized vol > 25% annualized
CRI_COR1M_THRESHOLD: float = 60.0         # implied correlation > 60
#: SPX below its 100-day MA — the snapshot stores distance as a signed percent.
CRI_SPX_BELOW_MA_PCT: float = 0.0


def cri_entry_rule(history: Sequence[SignalPoint], point: SignalPoint) -> float:
    """Documented CRI Crash Trigger (docs/strategies.md §Strategy 6).

    Uses ONLY the current origin's signal (a same-day regime read); the engine
    still guarantees the mark-to-market is the FORWARD return. Returns -1.0
    (short SPY == long SPY puts) when all three conditions fire, else 0.0.
    """
    signal = point.signal
    realized_vol = signal.get("realized_vol")
    cor1m = signal.get("cor1m")
    spx_vs_ma = signal.get("spx_vs_ma_pct")
    if realized_vol is None or cor1m is None or spx_vs_ma is None:
        return 0.0

    below_ma = float(spx_vs_ma) < CRI_SPX_BELOW_MA_PCT
    vol_stressed = float(realized_vol) > CRI_REALIZED_VOL_THRESHOLD
    correlation_high = float(cor1m) > CRI_COR1M_THRESHOLD
    if below_ma and vol_stressed and correlation_high:
        return -1.0
    return 0.0


@dataclass(frozen=True)
class Strategy:
    key: str
    label: str
    wired: bool
    entry_doc: str
    loader: Optional[Callable[[], tuple[list[SignalPoint], list[tuple[str, float]]]]] = None
    rule: Optional[Callable[[Sequence[SignalPoint], SignalPoint], float]] = None


STRATEGIES: dict[str, Strategy] = {
    "cri": Strategy(
        key="cri",
        label="Crash Risk Index (CRI)",
        wired=True,
        entry_doc=(
            "Crash Trigger: SPX < 100d MA AND 20d realized vol > 25% AND "
            "COR1M > 60. Buy SPY puts (modelled as short SPY)."
        ),
        loader=load_cri_series,
        rule=cri_entry_rule,
    ),
    "dark_pool_flow": Strategy(
        key="dark_pool_flow",
        label="Dark Pool Flow Detection",
        wired=False,
        entry_doc="3+ consecutive days same direction AND flow strength > 50.",
    ),
    "leap_iv": Strategy(
        key="leap_iv",
        label="LEAP IV Mispricing",
        wired=False,
        entry_doc="HV20 > LEAP IV by >=15-20 pts with a structural thesis.",
    ),
    "garch_convergence": Strategy(
        key="garch_convergence",
        label="GARCH Convergence Spreads",
        wired=False,
        entry_doc="IV/HV60 leader-vs-lagger divergence >= 0.15.",
    ),
    "risk_reversal": Strategy(
        key="risk_reversal",
        label="Risk Reversal",
        wired=False,
        entry_doc="Operator-directed; IV skew >= 3% at target delta.",
    ),
    "vcg": Strategy(
        key="vcg",
        label="Volatility-Credit Gap (VCG-R)",
        wired=False,
        entry_doc="VIX > 28 AND VCG z-score > 2.5 AND both betas < 0.",
    ),
}


def list_strategies() -> list[dict[str, Any]]:
    """Registry view for the API: every strategy with its wired flag + doc."""
    return [
        {
            "key": s.key,
            "label": s.label,
            "wired": s.wired,
            "entry_doc": s.entry_doc,
        }
        for s in STRATEGIES.values()
    ]


def run_strategy(
    key: str,
    *,
    horizon: int = 1,
    cost_fraction: float = 0.0,
    history: Optional[list[dict]] = None,
) -> dict:
    """Run a wired strategy end-to-end and return trades + metrics.

    Raises ``KeyError`` for an unknown key and ``NotImplementedError`` for a
    registered-but-stubbed strategy — never a fake result.
    """
    strategy = STRATEGIES.get(key)
    if strategy is None:
        raise KeyError(f"unknown strategy: {key}")
    if not strategy.wired or strategy.loader is None or strategy.rule is None:
        raise NotImplementedError(
            f"strategy '{key}' is registered but not yet wired for backtesting"
        )

    if key == "cri" and history is not None:
        points, underlying = load_cri_series(history)
    else:
        points, underlying = strategy.loader()

    forward = forward_returns_from_underlying(underlying, horizon=horizon)
    result = walk_forward_backtest(
        points, forward, strategy.rule, cost_fraction=cost_fraction
    )
    result["strategy"] = key
    result["label"] = strategy.label
    result["horizon"] = horizon
    result["wired"] = True
    return result
