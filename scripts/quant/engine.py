"""Rebalance calendar, look-ahead-free weights backtest, vol-target overlay.

Semantics (the whole no-look-ahead guarantee lives in the loop order):

* ``targets[t]`` is the weight vector a strategy wants at the CLOSE of session
  ``t``, computed from data dated <= ``t``.
* On session ``t`` the engine first marks the book carried from ``t - 1`` to
  market (the return from ``t - 1`` to ``t``), lets the weights drift with
  prices, and only THEN, if ``t`` is a rebalance session, trades to
  ``targets[t]`` and pays ``cost_bps`` on the turnover. The new weights earn
  the return from ``t`` to ``t + 1``.
* A target set on the last session therefore never affects equity.

Pure logic: numpy, no DB, no network. Metrics reuse ``backtest.metrics``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional, Sequence

import numpy as np
from backtest.metrics import compute_metrics

from .indicators import TRADING_DAYS_PER_YEAR, as_matrix, pct_change

FREQUENCIES = ("D", "W", "BME", "BMS", "BQE", "SIGNAL")


@dataclass(frozen=True)
class RebalancePolicy:
    """When the book is traded to its target.

    ``D`` every session, ``W`` the last session of each ISO week, ``BME`` the
    last session of each month, ``BMS`` the first session of each month,
    ``BQE`` the last session of each calendar quarter, ``SIGNAL`` whenever the
    target moves more than ``signal_change_threshold`` (max abs weight change)
    from the last applied target.
    """

    frequency: str = "BME"
    signal_change_threshold: float = 0.01

    def __post_init__(self) -> None:
        if self.frequency not in FREQUENCIES:
            raise ValueError(f"unknown rebalance frequency {self.frequency!r}; expected one of {FREQUENCIES}")

    def to_dict(self) -> dict[str, Any]:
        return {"frequency": self.frequency, "signal_change_threshold": self.signal_change_threshold}


def _next_weekday(day: date) -> date:
    nxt = day + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def rebalance_mask(dates: Sequence[str], policy: RebalancePolicy) -> np.ndarray:
    """Boolean mask of rebalance sessions.

    The period end of the LAST row is judged against the next weekday on the
    calendar (holidays ignored), so a month-end read on its own session still
    reports ``True`` in targets mode. ``D`` and ``SIGNAL`` return all ``True``;
    the engine applies the SIGNAL threshold inline.
    """
    n = len(dates)
    if policy.frequency in ("D", "SIGNAL"):
        return np.ones(n, dtype=bool)
    parsed = [date.fromisoformat(d) for d in dates]
    mask = np.zeros(n, dtype=bool)
    for i, cur in enumerate(parsed):
        nxt = parsed[i + 1] if i + 1 < n else _next_weekday(cur)
        if policy.frequency == "BME":
            mask[i] = (cur.year, cur.month) != (nxt.year, nxt.month)
        elif policy.frequency == "BQE":
            mask[i] = (cur.year, (cur.month - 1) // 3) != (nxt.year, (nxt.month - 1) // 3)
        elif policy.frequency == "W":
            mask[i] = cur.isocalendar()[:2] != nxt.isocalendar()[:2]
        elif policy.frequency == "BMS":
            prev = parsed[i - 1] if i > 0 else None
            mask[i] = prev is None or (prev.year, prev.month) != (cur.year, cur.month)
    return mask


def run_weights_backtest(
    closes,
    dates: Sequence[str],
    targets,
    *,
    policy: RebalancePolicy,
    cost_bps: float = 5.0,
    initial_capital: float = 100_000.0,
    max_gross: float = 1.0,
    symbols: Optional[Sequence[str]] = None,
    rebalance_first_session: bool = False,
) -> dict[str, Any]:
    """Replay ``targets`` over ``closes`` and return equity, returns, turnover,
    final weights and a JSON-serialisable metrics bundle.

    A symbol with no finite close on a rebalance session gets weight 0 (it
    cannot be held). A missing close on either side of a session contributes
    a zero return for that symbol. Gross exposure above ``max_gross`` is scaled
    down proportionally. Costs are ``cost_bps`` of traded notional per side.
    """
    px = as_matrix(closes)
    tgt_all = np.nan_to_num(as_matrix(targets), nan=0.0)
    if px.shape != tgt_all.shape:
        raise ValueError(f"closes {px.shape} and targets {tgt_all.shape} differ in shape")
    n_sessions, n_assets = px.shape
    if n_sessions != len(dates):
        raise ValueError("dates must have one entry per row of closes")
    names = [str(s) for s in symbols] if symbols is not None else [str(i) for i in range(n_assets)]

    mask = rebalance_mask(dates, policy)
    if rebalance_first_session:
        mask[0] = True
    signal_mode = policy.frequency == "SIGNAL"

    weights = np.zeros(n_assets)
    last_applied = np.zeros(n_assets)
    equity = np.empty(n_sessions)
    returns = np.zeros(n_sessions)
    turnover = np.zeros(n_sessions)
    gross_path = np.zeros(n_sessions)
    net_path = np.zeros(n_sessions)
    n_rebalances = 0
    cost_total = 0.0
    level = float(initial_capital)

    for t in range(n_sessions):
        if t > 0:
            prev, cur = px[t - 1], px[t]
            ok = np.isfinite(prev) & np.isfinite(cur) & (prev > 0)
            r = np.where(ok, cur / np.where(ok, prev, 1.0) - 1.0, 0.0)
            port = float(weights @ r)
            level *= 1.0 + port
            weights = np.where(1.0 + port != 0.0, weights * (1.0 + r) / (1.0 + port), 0.0)

        do_rebalance = bool(mask[t])
        if signal_mode:
            do_rebalance = bool(np.max(np.abs(tgt_all[t] - last_applied)) > policy.signal_change_threshold)
        if do_rebalance:
            target = tgt_all[t].copy()
            target[~np.isfinite(px[t])] = 0.0
            gross = float(np.abs(target).sum())
            if gross > max_gross > 0:
                target *= max_gross / gross
            traded = float(np.abs(target - weights).sum())
            if traded > 1e-12:
                cost = traded * cost_bps / 10_000.0
                level *= 1.0 - cost
                cost_total += cost
                n_rebalances += 1
                turnover[t] = traded
            weights = target
            last_applied = target

        equity[t] = level
        returns[t] = level / (equity[t - 1] if t > 0 else float(initial_capital)) - 1.0
        gross_path[t] = float(np.abs(weights).sum())
        net_path[t] = float(weights.sum())

    metrics = compute_metrics(returns.tolist(), periods_per_year=TRADING_DAYS_PER_YEAR)
    metrics.pop("equity_curve", None)
    metrics.pop("n_trades", None)
    daily_std = float(returns.std(ddof=0)) if n_sessions > 1 else 0.0
    metrics.update(
        {
            "ann_vol": daily_std * math.sqrt(TRADING_DAYS_PER_YEAR),
            "total_return": float(level / initial_capital - 1.0),
            "avg_turnover": float(turnover[turnover > 0].mean()) if n_rebalances else 0.0,
            "cost_drag": cost_total,
            "mean_gross": float(gross_path.mean()),
            "mean_net": float(net_path.mean()),
        }
    )
    return {
        "dates": list(dates),
        "equity": equity.tolist(),
        "returns": returns.tolist(),
        "turnover": turnover.tolist(),
        "n_sessions": n_sessions,
        "n_rebalances": n_rebalances,
        "final_weights": {name: float(w) for name, w in zip(names, weights)},
        "policy": policy.to_dict(),
        "cost_bps": cost_bps,
        "initial_capital": float(initial_capital),
        "max_gross": max_gross,
        "metrics": metrics,
    }


def latest_targets(dates: Sequence[str], symbols: Sequence[str], targets) -> dict[str, Any]:
    """The strategy's current target: the last row of ``targets`` by symbol."""
    row = np.nan_to_num(as_matrix(targets)[-1], nan=0.0)
    weights = {str(s): float(w) for s, w in zip(symbols, row)}
    return {
        "as_of": dates[-1],
        "weights": weights,
        "gross": float(np.abs(row).sum()),
        "net": float(row.sum()),
    }


def apply_vol_target(
    targets,
    closes,
    dates: Optional[Sequence[str]] = None,
    *,
    target_vol: float = 0.10,
    lookback: int = 63,
    max_leverage: float = 1.0,
    rescore: str = "BMS",
) -> np.ndarray:
    """Scale every row so the book's realised vol approaches ``target_vol``.

    Port of QuantJourney ``VolTargetModel``: the realised vol is the sample
    std of the unit-exposure book's returns over the ``lookback`` sessions
    STRICTLY BEFORE the row, annualised; ``scale = min(target / realised,
    max_leverage)``, held at 1.0 when realised vol is below 1%. The scale is
    recomputed on rescore sessions only (``BMS`` first session of each month
    when ``dates`` are given, else every session) and carried between them.
    Rows before ``lookback`` are flat.
    """
    tgt = np.nan_to_num(as_matrix(targets), nan=0.0)
    rets = np.nan_to_num(pct_change(closes), nan=0.0)
    n = tgt.shape[0]
    out = np.zeros_like(tgt)
    if rescore == "BMS" and dates is not None:
        is_rescore = rebalance_mask(dates, RebalancePolicy("BMS"))
    else:
        is_rescore = np.ones(n, dtype=bool)
    scale = 1.0
    for i in range(lookback, n):
        row = tgt[i]
        total = float(np.abs(row).sum())
        if total < 1e-8:
            continue
        if is_rescore[i]:
            unit = row / total
            port = rets[i - lookback : i] @ unit
            realised = float(np.std(port, ddof=1)) * math.sqrt(TRADING_DAYS_PER_YEAR)
            scale = min(target_vol / realised, max_leverage) if realised > 0.01 else 1.0
        out[i] = row * scale
    return out
