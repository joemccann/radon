"""Performance-metrics primitives for the strategy backtester.

Pure functions over a sequence of per-trade (or per-period) simple returns.
numpy allowed; no torch / chronos / DB. Every metric is a query (returns a
value, no side effects).

Conventions
-----------
- ``returns`` are per-period SIMPLE returns (0.01 == +1%).
- Ratios use POPULATION std (ddof=0) so a hand-derived value is unambiguous.
- Degenerate inputs (empty series, zero dispersion, no drawdown) return 0.0
  rather than raising or yielding inf/nan — a backtest summary must stay
  JSON-serialisable.
"""

from __future__ import annotations

from typing import Sequence

#: Trading days per year — the default annualisation factor.
TRADING_DAYS_PER_YEAR: int = 252


def equity_curve(returns: Sequence[float], start: float = 1.0) -> list[float]:
    """Compounded equity path. Element i is equity AFTER return i.

    A non-empty series yields one point per return (no leading anchor). An
    empty series yields ``[start]`` so callers always have a usable point.
    """
    if not returns:
        return [float(start)]
    curve: list[float] = []
    level = float(start)
    for r in returns:
        level *= 1.0 + float(r)
        curve.append(level)
    return curve


def max_drawdown(curve: Sequence[float]) -> float:
    """Worst peak-to-trough decline of an equity curve, as a negative fraction.

    Returns 0.0 for a monotonically non-decreasing curve (or fewer than 2
    points). The value is <= 0.
    """
    if len(curve) < 2:
        return 0.0
    peak = float(curve[0])
    worst = 0.0
    for value in curve:
        value = float(value)
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = value / peak - 1.0
            if drawdown < worst:
                worst = drawdown
    return worst


def sharpe(
    returns: Sequence[float],
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
    risk_free_annual: float = 0.0,
) -> float:
    """Annualised Sharpe ratio using population std.

    ``risk_free_annual`` is converted to a per-period rate by dividing by
    ``periods_per_year`` and subtracted from each return. Zero dispersion
    returns 0.0.
    """
    import numpy as np

    if len(returns) == 0:
        return 0.0
    arr = np.asarray([float(r) for r in returns], dtype=float)
    rf_per_period = float(risk_free_annual) / periods_per_year
    excess = arr - rf_per_period
    std = float(excess.std(ddof=0))
    if std == 0.0:
        return 0.0
    return float(excess.mean() / std) * (periods_per_year ** 0.5)


def sortino(
    returns: Sequence[float],
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
    risk_free_annual: float = 0.0,
) -> float:
    """Annualised Sortino ratio: excess mean over downside deviation.

    Downside deviation is ``sqrt(mean(min(excess, 0)^2))`` averaged over ALL
    periods (the conventional definition). No downside returns 0.0.
    """
    import numpy as np

    if len(returns) == 0:
        return 0.0
    arr = np.asarray([float(r) for r in returns], dtype=float)
    rf_per_period = float(risk_free_annual) / periods_per_year
    excess = arr - rf_per_period
    downside = np.minimum(excess, 0.0)
    downside_std = float((downside ** 2).mean() ** 0.5)
    if downside_std == 0.0:
        return 0.0
    return float(excess.mean() / downside_std) * (periods_per_year ** 0.5)


def annualized_return(
    returns: Sequence[float],
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Geometric annualised return from a sequence of per-period returns."""
    if len(returns) == 0:
        return 0.0
    total_growth = 1.0
    for r in returns:
        total_growth *= 1.0 + float(r)
    if total_growth <= 0.0:
        return -1.0
    return total_growth ** (periods_per_year / len(returns)) - 1.0


def calmar(
    returns: Sequence[float],
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Calmar ratio: annualised return over the magnitude of max drawdown.

    Zero drawdown returns 0.0 (ratio undefined).
    """
    curve = equity_curve(returns)
    dd = max_drawdown(curve)
    if dd == 0.0:
        return 0.0
    ann = annualized_return(returns, periods_per_year=periods_per_year)
    return ann / abs(dd)


def hit_rate(returns: Sequence[float]) -> float:
    """Fraction of STRICTLY positive returns. Empty series returns 0.0."""
    if len(returns) == 0:
        return 0.0
    wins = sum(1 for r in returns if float(r) > 0.0)
    return wins / len(returns)


def expectancy(returns: Sequence[float]) -> float:
    """Mean per-trade return (expected value of one trade)."""
    if len(returns) == 0:
        return 0.0
    return sum(float(r) for r in returns) / len(returns)


def compute_metrics(
    returns: Sequence[float],
    *,
    periods_per_year: int = TRADING_DAYS_PER_YEAR,
    risk_free_annual: float = 0.0,
) -> dict:
    """Bundle every metric into one JSON-serialisable summary dict."""
    curve = equity_curve(returns)
    return {
        "n_trades": len(returns),
        "sharpe": sharpe(
            returns,
            periods_per_year=periods_per_year,
            risk_free_annual=risk_free_annual,
        ),
        "sortino": sortino(
            returns,
            periods_per_year=periods_per_year,
            risk_free_annual=risk_free_annual,
        ),
        "calmar": calmar(returns, periods_per_year=periods_per_year),
        "max_drawdown": max_drawdown(curve),
        "hit_rate": hit_rate(returns),
        "expectancy": expectancy(returns),
        "annualized_return": annualized_return(
            returns, periods_per_year=periods_per_year
        ),
        "equity_curve": curve,
    }
