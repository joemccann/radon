"""After-hours order placement: the IB "held until next open" warning (399) and
the "outside-RTH attribute ignored" warning (2109) must NOT block placement.

Treating 399 as an error was why after-hours orders returned status:error and
"couldn't be placed" (2026-06-22). Rejections (201/202/…) must still block.
"""
from __future__ import annotations

from ib_place_order import NON_BLOCKING_IB_CODES


def test_after_hours_order_warnings_are_non_blocking():
    assert 399 in NON_BLOCKING_IB_CODES   # "will not be placed until <next open>"
    assert 2109 in NON_BLOCKING_IB_CODES  # "Outside Regular Trading Hours ignored"


def test_connection_informational_codes_still_non_blocking():
    for code in (2104, 2106, 2108, 2158, 10358):
        assert code in NON_BLOCKING_IB_CODES


def test_real_rejection_codes_are_NOT_suppressed():
    # Pre-trade risk reject / cancel must still surface as errors.
    for code in (201, 202, 10147, 10148):
        assert code not in NON_BLOCKING_IB_CODES
