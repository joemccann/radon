"""Regression: journal-gap-sli error on a journaled TQQQ stock fill (2026-10-09).

Production: `missing_exec_id_count=1`, gap `0002bd08.6ac773f7.01.01`. That
343-share fill is journaled as a fill-monitor row (no IB exec id) beside five
IB_AUTO_IMPORT rows for the same TQQQ day, so the per-contract fallback should
cover it. IB delivered the stock contract with `right="?"` (its unset-right
placeholder), and the executed-side key `('TQQQ', '', '?', '')` never matched
the journal's `('TQQQ', '', '', '')`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from monitor_daemon.handlers.journal_reconcile import (  # noqa: E402
    _extract_contract_key,
    _find_gaps,
)

STOCK_FILL = {
    "exec_id": "0002bd08.6ac773f7.01.01",
    "fill_time": "2026-10-08T22:34:28+00:00",
    "payload": {
        "execId": "0002bd08.6ac773f7.01.01",
        "symbol": "TQQQ",
        "contract": {"conId": 72539702, "symbol": "TQQQ", "secType": "STK",
                     "strike": 0.0, "right": "?", "expiry": None},
        "side": "BOT",
        "quantity": 343.0,
    },
}


def test_stock_right_placeholder_keys_like_an_unset_right():
    assert _extract_contract_key(STOCK_FILL["payload"]) == ("TQQQ", "", "", "")


def test_journaled_stock_day_covers_a_placeholder_right_fill():
    coverage = {"exec_ids": set(), "contract_dates": {("TQQQ", "", "", "", "2026-10-08")}}

    assert _find_gaps([STOCK_FILL], coverage) == []
