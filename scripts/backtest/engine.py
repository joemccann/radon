"""Walk-forward backtest engine — look-ahead-free by construction.

The engine replays a chronologically ordered signal series. At each origin
``t`` it hands the entry rule the history of signal points dated <= t (and only
those). The rule returns a desired position in {-1, 0, +1} (a fractional weight
is also accepted). A non-flat position is marked to market against the FORWARD
return keyed at ``t`` — the return realised over the holding window that starts
after the decision is made. This keying is the whole no-look-ahead guarantee:
the decision uses data up to t, and the P&L it earns is the return that follows
t, so the realised mark never feeds back into the decision.

Costs are a per-trade fraction subtracted from the gross return (the caller
supplies it; ``scripts/costs.py`` produces a dollar figure that the strategy
layer converts to a fraction of premium — see ``strategies.py``).

Pure logic: numpy not even required here. No torch / chronos / DB / network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from .metrics import compute_metrics

#: An entry rule: (history_up_to_and_including_origin, origin_point) -> position.
#: Position is a signed weight; sign is direction, magnitude scales the return.
EntryRule = Callable[[Sequence["SignalPoint"], "SignalPoint"], float]


@dataclass(frozen=True)
class SignalPoint:
    """One dated row of a replayed signal series.

    ``signal`` is the parsed per-strategy payload for that date (a dict). The
    engine never inspects its contents — only the strategy's entry rule does.
    """

    date: str
    signal: Mapping[str, Any]


def walk_forward_backtest(
    series: Sequence[SignalPoint],
    forward_returns: Mapping[str, float],
    entry_rule: EntryRule,
    *,
    cost_fraction: float = 0.0,
    periods_per_year: int = 252,
) -> dict:
    """Replay ``series`` in date order, applying ``entry_rule`` at each origin.

    Parameters
    ----------
    series:
        Chronologically ascending signal points. Sorted defensively here.
    forward_returns:
        Map of origin date -> realised forward return for a position opened at
        that origin. An origin missing from this map cannot be marked to
        market and is skipped (typically the most recent bars with no future
        yet).
    entry_rule:
        Sees ONLY ``series[: i + 1]`` at origin i. Returns a signed position.
    cost_fraction:
        Round-trip cost as a fraction of notional, subtracted from the gross
        return of every non-flat trade.

    Returns a dict with the per-trade list and the aggregate metrics bundle.
    """
    ordered = sorted(series, key=lambda p: p.date)
    trades: list[dict] = []

    for i, point in enumerate(ordered):
        history = ordered[: i + 1]  # <= origin only — the no-look-ahead window
        position = float(entry_rule(history, point))
        if position == 0.0:
            continue
        if point.date not in forward_returns:
            continue  # cannot mark to market without a forward return

        forward = float(forward_returns[point.date])
        gross = position * forward
        net = gross - cost_fraction
        trades.append(
            {
                "date": point.date,
                "position": position,
                "forward_return": forward,
                "gross_return": gross,
                "net_return": net,
            }
        )

    net_returns = [t["net_return"] for t in trades]
    metrics = compute_metrics(net_returns, periods_per_year=periods_per_year)
    return {"trades": trades, "metrics": metrics}
