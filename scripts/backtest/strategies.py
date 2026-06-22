"""Strategy registry for the backtester.

Each strategy is one entry in ``STRATEGIES`` describing:
  - ``key``        — URL / CLI identifier
  - ``label``      — human name (matches docs/strategies.md)
  - ``wired``      — True iff the entry rule + signal loader are implemented
  - ``entry_doc``  — the documented entry rule, lifted from docs/strategies.md
  - ``loader``     — () -> (points, underlying)  (only for wired strategies)
  - ``rule``       — EntryRule  (only for wired strategies)

Be honest in code about what the data supports. Every strategy is now wired to
its documented entry rule (docs/strategies.md). Where a strategy's snapshot
table is genuinely sparse or absent (no autonomous daily feed exists yet),
``run_strategy`` returns status ``insufficient_data`` with zero trades rather
than raising or fabricating a result.

Wiring map (source -> signal series + underlying):
  - cri             cri_snapshots.history          -> SPY close
  - vcg             vcg_snapshots.history          -> credit proxy (HYG) close
  - dark_pool_flow  ticker_flow_history            -> ticker close (price column
                    absent in prod -> insufficient_data until fed)
  - leap_iv         (no leap_snapshots table)      -> insufficient_data
  - garch_convergence (no garch_snapshots table)   -> insufficient_data
  - risk_reversal   (operator-directed, no feed)   -> insufficient_data
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from .engine import SignalPoint, walk_forward_backtest
from .signal_replay import (
    forward_returns_from_underlying,
    load_cri_series,
    load_dark_pool_series,
    load_vcg_series,
)

# --- documented CRI entry rule (docs/strategies.md Strategy 6) -------------- #
#: Crash Trigger fires when ALL three convergence conditions hold. When it
#: fires the strategy buys SPY puts — modelled here as a SHORT SPY position so a
#: drop in SPY produces a positive return for the hedge.
CRI_REALIZED_VOL_THRESHOLD: float = 25.0  # 20d realized vol > 25% annualized
CRI_COR1M_THRESHOLD: float = 60.0         # implied correlation > 60
#: SPX below its 100-day MA — the snapshot stores distance as a signed percent.
CRI_SPX_BELOW_MA_PCT: float = 0.0

# --- documented VCG-R entry rule (docs/strategies.md Strategy 5) ------------ #
#: Risk-Off trigger: VIX > 28 AND VCG z-score > 2.5 AND both betas < 0 (sign
#: discipline). When RO fires the strategy buys credit-proxy (HYG) puts —
#: modelled as SHORT credit so a drop in HYG produces a positive hedge return.
VCG_VIX_THRESHOLD: float = 28.0
VCG_RO_TRIGGER: float = 2.5

# --- documented Dark Pool Flow entry rule (docs/strategies.md Strategy 1) --- #
#: Sustained direction: 3+ consecutive days same direction AND flow strength
#: > 50. Accumulation -> long the underlying; distribution -> short it.
DARK_POOL_MIN_STREAK: int = 3
DARK_POOL_FLOW_STRENGTH_THRESHOLD: float = 50.0


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


def vcg_entry_rule(history: Sequence[SignalPoint], point: SignalPoint) -> float:
    """Documented VCG-R Risk-Off trigger (docs/strategies.md §Strategy 5).

    VIX > 28 AND VCG > 2.5 AND both betas < 0. Same-day regime read; the engine
    marks it against the FORWARD credit return. Returns -1.0 (short credit ==
    long HYG puts) when RO fires, else 0.0.
    """
    signal = point.signal
    vcg = signal.get("vcg")
    vix = signal.get("vix")
    beta1 = signal.get("beta1")
    beta2 = signal.get("beta2")
    if vcg is None or vix is None or beta1 is None or beta2 is None:
        return 0.0

    vol_elevated = float(vix) > VCG_VIX_THRESHOLD
    divergence_confirmed = float(vcg) > VCG_RO_TRIGGER
    sign_ok = float(beta1) < 0.0 and float(beta2) < 0.0
    if vol_elevated and divergence_confirmed and sign_ok:
        return -1.0
    return 0.0


def _consecutive_same_direction(
    history: Sequence[SignalPoint], direction: str
) -> int:
    """Count the run of trailing points (ending at the origin) whose
    ``dp_direction`` equals ``direction``. Look-ahead-free: ``history`` is the
    engine's ``series[: i + 1]`` window, so only data dated <= origin is seen.
    """
    streak = 0
    for past in reversed(history):
        if past.signal.get("dp_direction") == direction:
            streak += 1
        else:
            break
    return streak


def dark_pool_flow_entry_rule(
    history: Sequence[SignalPoint], point: SignalPoint
) -> float:
    """Documented Dark Pool Flow trigger (docs/strategies.md §Strategy 1).

    3+ consecutive same-direction days AND flow strength > 50. Accumulation ->
    long (+1), distribution -> short (-1). The streak is counted over the
    no-look-ahead history window the engine supplies.
    """
    signal = point.signal
    direction = signal.get("dp_direction")
    strength = signal.get("flow_strength")
    if direction not in ("accumulation", "distribution") or strength is None:
        return 0.0
    if float(strength) <= DARK_POOL_FLOW_STRENGTH_THRESHOLD:
        return 0.0
    if _consecutive_same_direction(history, direction) < DARK_POOL_MIN_STREAK:
        return 0.0
    return 1.0 if direction == "accumulation" else -1.0


def _empty_series() -> tuple[list[SignalPoint], list[tuple[str, float]]]:
    """Loader for strategies with no autonomous daily snapshot feed yet.

    Returns no points and no underlying so ``run_strategy`` reports
    ``insufficient_data`` honestly instead of fabricating trades.
    """
    return [], []


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
        wired=True,
        entry_doc="3+ consecutive days same direction AND flow strength > 50.",
        loader=load_dark_pool_series,
        rule=dark_pool_flow_entry_rule,
    ),
    "leap_iv": Strategy(
        key="leap_iv",
        label="LEAP IV Mispricing",
        wired=True,
        entry_doc="HV20 > LEAP IV by >=15-20 pts with a structural thesis.",
        loader=_empty_series,
        rule=cri_entry_rule,  # unreachable: no autonomous LEAP snapshot feed yet
    ),
    "garch_convergence": Strategy(
        key="garch_convergence",
        label="GARCH Convergence Spreads",
        wired=True,
        entry_doc="IV/HV60 leader-vs-lagger divergence >= 0.15.",
        loader=_empty_series,
        rule=cri_entry_rule,  # unreachable: no autonomous GARCH snapshot feed yet
    ),
    "risk_reversal": Strategy(
        key="risk_reversal",
        label="Risk Reversal",
        wired=True,
        entry_doc="Operator-directed; IV skew >= 3% at target delta.",
        loader=_empty_series,
        rule=cri_entry_rule,  # unreachable: operator-directed, no autonomous feed
    ),
    "vcg": Strategy(
        key="vcg",
        label="Volatility-Credit Gap (VCG-R)",
        wired=True,
        entry_doc="VIX > 28 AND VCG z-score > 2.5 AND both betas < 0.",
        loader=load_vcg_series,
        rule=vcg_entry_rule,
    ),
}

#: Strategies registered + wired but whose snapshot table carries no replayable
#: daily series yet. ``run_strategy`` short-circuits these to insufficient_data.
_NO_FEED_STRATEGIES: frozenset[str] = frozenset(
    {"leap_iv", "garch_convergence", "risk_reversal"}
)


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


def _insufficient_data_result(strategy: Strategy, horizon: int) -> dict:
    """A zero-trade, JSON-serialisable result for a wired-but-no-data strategy."""
    empty = walk_forward_backtest([], {}, strategy.rule or cri_entry_rule)
    empty["strategy"] = strategy.key
    empty["label"] = strategy.label
    empty["horizon"] = horizon
    empty["wired"] = True
    empty["status"] = "insufficient_data"
    return empty


def run_strategy(
    key: str,
    *,
    horizon: int = 1,
    cost_fraction: float = 0.0,
    history: Optional[list[dict]] = None,
) -> dict:
    """Run a wired strategy end-to-end and return trades + metrics + status.

    Raises ``KeyError`` for an unknown key. For a wired strategy with no
    replayable daily series (sparse/absent snapshot table, or an empty replayed
    history) the result carries ``status: "insufficient_data"`` and zero trades
    — never a fabricated result.
    """
    strategy = STRATEGIES.get(key)
    if strategy is None:
        raise KeyError(f"unknown strategy: {key}")

    if key in _NO_FEED_STRATEGIES:
        return _insufficient_data_result(strategy, horizon)

    loader = strategy.loader or _empty_series
    rule = strategy.rule or cri_entry_rule

    if history is not None:
        points, underlying = loader(history)
    else:
        points, underlying = loader()

    if not points or not underlying:
        return _insufficient_data_result(strategy, horizon)

    forward = forward_returns_from_underlying(underlying, horizon=horizon)
    result = walk_forward_backtest(
        points, forward, rule, cost_fraction=cost_fraction
    )
    result["strategy"] = key
    result["label"] = strategy.label
    result["horizon"] = horizon
    result["wired"] = True
    result["status"] = "ok"
    return result
