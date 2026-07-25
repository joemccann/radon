"""Point & Figure signal engine — close-only method, traditional box
scaling, 3-box reversal (tasks/bpi-indicator-plan.md section 3).

Feeds the Bullish Percent Index: a member is "bullish" while its most
recent P&F signal is a double-top BUY (current X column strictly exceeds
the previous X column's top) and "bearish" after a double-bottom SELL.
Close-only is a documented deviation from StockCharts' high/low charts —
the 30/70 regime read is insensitive to the method delta.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import date
from functools import lru_cache

# Traditional box scale: (band upper bound, box size). The ladder is the
# concatenation of these rungs; a column's geometry follows the rungs, so
# box size changes automatically as price crosses a band boundary.
_BANDS: tuple[tuple[float, float], ...] = (
    (0.25, 0.0625),
    (1.0, 0.125),
    (5.0, 0.25),
    (20.0, 0.5),
    (100.0, 1.0),
    (200.0, 2.0),
    (100_000.0, 4.0),
)

_REVERSAL_BOXES = 3


@lru_cache(maxsize=1)
def traditional_box_ladder() -> tuple[float, ...]:
    """All rung prices from the smallest band up through $100k. Each band
    steps from its own lower boundary so rungs land on round values
    (…, 19.5, 20, 21, 22, …), matching the traditional scale."""
    rungs: list[float] = []
    lower = 0.0
    for upper, box in _BANDS:
        price = round(lower + box, 4)
        while price <= upper + 1e-9:
            rungs.append(round(price, 4))
            price = round(price + box, 4)
        lower = upper
    return tuple(rungs)


def floor_rung(price: float) -> int:
    """Index of the highest rung <= price (X-column box fill)."""
    ladder = traditional_box_ladder()
    idx = bisect_right(ladder, price) - 1
    return max(idx, 0)


def ceil_rung(price: float) -> int:
    """Index of the lowest rung >= price (O-column box fill)."""
    ladder = traditional_box_ladder()
    idx = bisect_left(ladder, price)
    return min(idx, len(ladder) - 1)


def signal_state_series(dates: list[date], closes: list[float]) -> list[str | None]:
    """Per-date P&F signal state: "buy", "sell", or None before the first
    resolved signal. State persists until the opposite breakout fires.

    Column walk (close-only): extension is checked before reversal; a
    reversal needs a move of >= 3 boxes off the current column extreme; a
    breakout must STRICTLY exceed the previous same-direction column's
    extreme (equal top/bottom is not a signal).
    """
    if len(dates) != len(closes):
        raise ValueError("dates and closes must align")

    ladder = traditional_box_ladder()
    states: list[str | None] = []
    state: str | None = None

    direction: str | None = None  # "X" | "O"; None until first reversal resolves
    cur_top = 0     # rung idx, X column extreme
    cur_bot = 0     # rung idx, O column extreme
    prev_x_top: int | None = None
    prev_o_bot: int | None = None

    for i, close in enumerate(closes):
        if close is None or close <= 0:
            states.append(state)
            continue

        if direction is None:
            # First column direction = whichever way price first travels
            # >= 3 boxes from the FIRST close (fixed anchor — an anchor that
            # trailed the price would never accumulate 3 boxes on gradual
            # moves and the walk would stay unresolved forever).
            if i == 0:
                cur_top = floor_rung(close)
                cur_bot = ceil_rung(close)
            elif floor_rung(close) >= cur_bot + _REVERSAL_BOXES:
                direction = "X"
                cur_top = floor_rung(close)
            elif ceil_rung(close) <= cur_top - _REVERSAL_BOXES:
                direction = "O"
                cur_bot = ceil_rung(close)
        elif direction == "X":
            if floor_rung(close) > cur_top:
                cur_top = floor_rung(close)
                if prev_x_top is not None and cur_top > prev_x_top and state != "buy":
                    state = "buy"
            elif ceil_rung(close) <= cur_top - _REVERSAL_BOXES:
                prev_x_top = cur_top
                direction = "O"
                cur_bot = ceil_rung(close)
                if prev_o_bot is not None and cur_bot < prev_o_bot and state != "sell":
                    state = "sell"
        else:  # direction == "O"
            if ceil_rung(close) < cur_bot:
                cur_bot = ceil_rung(close)
                if prev_o_bot is not None and cur_bot < prev_o_bot and state != "sell":
                    state = "sell"
            elif floor_rung(close) >= cur_bot + _REVERSAL_BOXES:
                prev_o_bot = cur_bot
                direction = "X"
                cur_top = floor_rung(close)
                if prev_x_top is not None and cur_top > prev_x_top and state != "buy":
                    state = "buy"

        states.append(state)

    _ = ladder  # ladder cached; retained for readability of the walk above
    return states


def current_signal(dates: list[date], closes: list[float]) -> str | None:
    """Latest resolved signal state for a member, or None."""
    series = signal_state_series(dates, closes)
    return series[-1] if series else None
