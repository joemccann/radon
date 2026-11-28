"""Annualized close-to-close historical vol used by the scanners.

Sample standard deviation of log returns (n - 1), times sqrt(252), in
percentage points. A non-positive close is left out of the return list.
`utils.realized_vol.compute_realized_vol` is the numpy contract for CRI
and RV-ratio. It does not drop those closes. The leap scanner also keeps
its own copy, which does not require the later close to be positive.
"""
from __future__ import annotations

import math
from typing import Optional, Sequence

ANNUALIZATION_SESSIONS = 252


def close_to_close_hv(prices: Sequence[float], period: int) -> Optional[float]:
    if len(prices) < period + 1:
        return None
    recent = list(prices)[-(period + 1):]
    returns = [
        math.log(recent[i] / recent[i - 1])
        for i in range(1, len(recent))
        if recent[i - 1] > 0 and recent[i] > 0
    ]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((item - mean) ** 2 for item in returns) / (len(returns) - 1)
    return math.sqrt(variance) * math.sqrt(ANNUALIZATION_SESSIONS) * 100
