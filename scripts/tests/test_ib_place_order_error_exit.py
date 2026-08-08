"""T-031 — the third exit of the confirm-poll loop: an errorEvent arriving
while permId is still 0 and the status is non-terminal.

The pre-fix return surfaced only a flat message string — no structured
ib_error_code / ib_error_text — so the operator (and journald) could not
distinguish a short-sale locate reject from a margin reject, and the
route's error envelope carried code=None.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from tests.test_spx01_grace_wait import (  # noqa: E402 — shared place-path harness
    _make_client,
    _make_trade,
    _invoke_place_order,
)

_STOCK_PARAMS = {
    "type": "stock",
    "symbol": "SPCX",
    "action": "SELL",
    "quantity": 100,
    "limitPrice": 25.0,
    "tif": "DAY",
}


def test_error_event_during_poll_with_zero_perm_id_returns_structured_ib_code():
    trade = _make_trade(status="PendingSubmit", perm_id=0)
    client = _make_client(
        trade,
        ib_errors_to_inject=[(201, "Order rejected - reason: shares not available for short sale")],
        inject_after_sleep_count=1,
    )

    result = _invoke_place_order(_STOCK_PARAMS, client)

    assert result["status"] == "error"
    assert result.get("ib_error_code") == 201, (
        f"structured IB code missing from the permId==0 error exit: {result}"
    )
    assert "short sale" in str(result.get("ib_error_text", ""))
    assert "201" in result["message"]


def test_error_exit_keeps_order_identity_fields():
    trade = _make_trade(status="PendingSubmit", perm_id=0)
    client = _make_client(
        trade,
        ib_errors_to_inject=[(110, "Price does not conform to minimum increment")],
        inject_after_sleep_count=1,
    )

    result = _invoke_place_order(_STOCK_PARAMS, client)

    assert result["status"] == "error"
    assert result.get("ib_error_code") == 110
    assert "orderId" in result
    assert result.get("permId") == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
