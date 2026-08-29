"""The per-minute placement budget is process-wide and leaked between tests.

`server._order_rate_timestamps` is a module-level deque holding every accepted
placement inside a rolling 60s window, capped by `RADON_MAX_ORDERS_PER_MIN`
(default 10). Nothing reset it between tests, and at least eight files in this
subtree POST `/orders/place`. Under CI's `-n auto --dist loadfile` one worker
runs several of those files back to back well inside 60s, so a later test
inherits a spent budget and gets 429 where it asserted 200:

    FAILED test_orders_place_safety_contract.py::
      test_orders_place_subprocess_timeout_fits_inside_next_budget
      - assert 429 == 200

Two files already cleared the deque by hand, which made the leak look handled
while every other file stayed exposed to it. The reset belongs in conftest.

These two tests are ORDER-DEPENDENT on purpose: the first spends the budget,
the second proves the next test did not inherit it.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR.parent))

import server  # noqa: E402


def test_a_test_may_spend_the_whole_placement_budget():
    from order_limits import max_orders_per_min

    now = time.monotonic()
    for index in range(max_orders_per_min()):
        server._order_rate_timestamps.append((now, f"leak-probe-{index}"))
    assert len(server._order_rate_timestamps) >= max_orders_per_min()


def test_the_next_test_starts_with_a_clean_budget():
    assert not server._order_rate_timestamps, (
        "the placement budget carried over from the previous test; the next "
        "POST /orders/place in this worker gets 429 instead of 200"
    )
