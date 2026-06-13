"""SPX-01 — RED/GREEN tests for the Inactive-status grace-wait in ib_place_order.py.

The SPCX short-sale rejection showed that IB's 201 errorEvent (shares not
available for short sale) arrives ASYNCHRONOUSLY — it can land AFTER the
confirm-poll loop breaks on `status == 'Inactive'`.  The pre-fix code checked
the error buffer exactly once (at poll time) and returned {"status":"error",
"message":"Order Inactive"} with no reason code, because the buffer was still
empty.

These tests drive the fix without placing any live order.  All IB interactions
are mocked.

Rules verified (from scripts/CLAUDE.md Order Placement Contract):
  * No disconnect while permId == 0 + limbo states  →  existing path unchanged.
  * stdout = JSON only  →  not directly tested here (unit concern).
  * Bounded waits  →  asserted via wall-clock timing; grace-wait < 3s.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import eventkit

# Ensure scripts/ is importable
_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_order(order_id: int = 99, perm_id: int = 12345) -> MagicMock:
    o = MagicMock()
    o.orderId = order_id
    o.permId = perm_id
    return o


def _make_order_status(status: str, why_held: str = "") -> MagicMock:
    os = MagicMock()
    os.status = status
    os.whyHeld = why_held
    return os


def _make_trade_log_entry(error_code: int = 0, message: str = "") -> object:
    from ib_insync import TradeLogEntry
    return TradeLogEntry(
        time=datetime.now(),
        status="Inactive",
        message=message,
        errorCode=error_code,
    )


def _make_trade(status: str = "Inactive", perm_id: int = 12345,
                log_entries: list | None = None) -> MagicMock:
    trade = MagicMock()
    trade.order = _make_order(order_id=99, perm_id=perm_id)
    trade.orderStatus = _make_order_status(status=status)
    trade.log = log_entries or []
    return trade


def _make_client(trade: MagicMock,
                 ib_errors_to_inject: list | None = None,
                 inject_after_sleep_count: int = 1) -> MagicMock:
    """Build a mock IBClient.

    Uses a real eventkit.Event for _ib.errorEvent so that the `+=` operator
    in ib_place_order.py registers the callback correctly.

    Injects error events during the Nth sleep() call (simulates async delivery
    after the terminal state was detected).
    """
    client = MagicMock()

    # Use a real eventkit.Event so += works correctly
    real_error_event = eventkit.Event("errorEvent")
    ib_mock = MagicMock()
    ib_mock.errorEvent = real_error_event
    client._ib = ib_mock

    client.place_order = MagicMock(return_value=trade)
    client.qualify_contracts = MagicMock(return_value=[MagicMock(conId=123456)])

    sleep_count = [0]

    def _sleep(duration: float) -> None:
        sleep_count[0] += 1
        if ib_errors_to_inject and sleep_count[0] >= inject_after_sleep_count:
            for (code, text) in ib_errors_to_inject:
                real_error_event.emit(99, code, text)

    client.sleep = MagicMock(side_effect=_sleep)
    client.disconnect = MagicMock()
    return client


# ---------------------------------------------------------------------------
# Invoke the function under test with IBClient patched
# ---------------------------------------------------------------------------

def _invoke_place_order(params: dict, client_mock: MagicMock,
                        _clock=None) -> dict:
    """Call ib_place_order.place_order() with IBClient replaced by client_mock.

    _clock: optional injectable clock for tests that need to control the
    confirm-poll deadline without real wall-clock waiting.
    """
    with patch("ib_place_order.IBClient", return_value=client_mock), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        import ib_place_order
        kwargs = {} if _clock is None else {"_clock": _clock}
        return ib_place_order.place_order(params, **kwargs)


_STOCK_PARAMS = {
    "type": "stock",
    "symbol": "SPCX",
    "action": "SELL",
    "quantity": 100,
    "limitPrice": 25.00,
    "tif": "DAY",
}


# ---------------------------------------------------------------------------
# SPX-01 Tests
# ---------------------------------------------------------------------------

class TestGraceWaitCapturesDelayed201:
    """201 errorEvent arrives after the poll loop breaks on Inactive status."""

    def test_grace_wait_captures_async_201_via_error_buffer(self):
        """Error fires on the 2nd sleep (after break) — grace-wait captures it."""
        trade = _make_trade(status="Inactive", perm_id=12345)
        # inject_after_sleep_count=2: first sleep is in the main confirm-poll
        # (which sees Inactive and breaks); the 2nd+ sleeps are grace-wait polls.
        client = _make_client(
            trade,
            ib_errors_to_inject=[(201, "Order rejected - Shares not available for short sale")],
            inject_after_sleep_count=2,
        )

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "error", f"Expected error, got: {result}"
        assert result.get("ib_error_code") == 201, f"Missing ib_error_code: {result}"
        assert "short sale" in result.get("ib_error_text", "").lower() or \
               "short sale" in result.get("message", "").lower(), \
               f"Missing 'short sale' reason in result: {result}"

    def test_grace_wait_includes_structured_code_and_text_fields(self):
        """ib_error_code + ib_error_text must be dedicated keys in the JSON."""
        trade = _make_trade(status="Inactive", perm_id=12345)
        client = _make_client(
            trade,
            ib_errors_to_inject=[(201, "Order rejected - Shares not available for short sale")],
            inject_after_sleep_count=2,
        )

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "error"
        assert result["ib_error_code"] == 201
        assert result["ib_error_text"] == "Order rejected - Shares not available for short sale"

    def test_grace_wait_is_bounded_does_not_hang(self):
        """When no error ever arrives, returns within a few seconds."""
        trade = _make_trade(status="Inactive", perm_id=12345)
        client = _make_client(trade, ib_errors_to_inject=None)

        t0 = time.monotonic()
        result = _invoke_place_order(_STOCK_PARAMS, client)
        elapsed = time.monotonic() - t0

        assert result["status"] == "error"
        assert elapsed < 5.0, f"Grace-wait hung: {elapsed:.1f}s"

    def test_grace_wait_timeout_returns_graceful_message_no_code(self):
        """No error arrives — message names the terminal state, ib_error_code absent."""
        trade = _make_trade(status="Inactive", perm_id=12345)
        client = _make_client(trade, ib_errors_to_inject=None)

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "error"
        assert "Inactive" in result["message"] or "inactive" in result["message"].lower(), \
               f"Message should name terminal state: {result}"
        assert result.get("ib_error_code") is None, \
               f"ib_error_code should be absent when no error arrived: {result}"

    def test_trade_log_fallback_when_error_buffer_empty(self):
        """Buffer empty but trade.log has errorCode entry → fallback captures it."""
        log_entry = _make_trade_log_entry(
            error_code=201,
            message="Order rejected - locate required",
        )
        trade = _make_trade(status="Inactive", perm_id=12345, log_entries=[log_entry])
        # No injected errors — buffer stays empty, must fall back to trade.log
        client = _make_client(trade, ib_errors_to_inject=None)

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "error"
        assert result.get("ib_error_code") == 201, \
               f"trade.log fallback did not surface code: {result}"
        assert "locate" in result.get("ib_error_text", "").lower() or \
               "locate" in result.get("message", "").lower(), \
               f"trade.log fallback missing reason: {result}"

    def test_rejected_status_also_gets_grace_wait(self):
        """Grace-wait fires for Rejected too, not just Inactive."""
        trade = _make_trade(status="Rejected", perm_id=12345)
        client = _make_client(
            trade,
            ib_errors_to_inject=[(201, "Order rejected - margin insufficient")],
            inject_after_sleep_count=2,
        )

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "error"
        assert result.get("ib_error_code") == 201, f"Rejected path missing code: {result}"

    def test_success_path_unchanged(self):
        """Submitted orders still return ok — regression guard."""
        trade = _make_trade(status="Submitted", perm_id=12345)
        client = _make_client(trade, ib_errors_to_inject=None)

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "ok", f"Expected ok, got: {result}"
        assert result["permId"] == 12345

    def test_no_grace_wait_on_perm_id_zero_limbo(self):
        """permId==0 + PendingSubmit hits the existing stuck-in-limbo path, not grace-wait.

        Uses an already-expired clock so the confirm-poll deadline fires
        immediately — no real wall-clock waiting required.
        """
        trade = _make_trade(status="PendingSubmit", perm_id=0)
        client = _make_client(trade, ib_errors_to_inject=None)

        # Expired-clock: returns a value that makes `_clock() < deadline` false
        # on the very first iteration.  The deadline is set as
        # `_clock() + 6.0`; if both calls return the same large constant the
        # loop body never executes once.
        _t = [0.0]

        def _expired_clock():
            v = _t[0]
            _t[0] += 7.0  # each call advances by 7 > the 6s deadline budget
            return v

        result = _invoke_place_order(_STOCK_PARAMS, client, _clock=_expired_clock)

        assert result["status"] == "error"
        # Must NOT have ib_error_code (that is only for the terminal-failed grace-wait path)
        assert result.get("ib_error_code") is None, \
               f"Should not grace-wait for PendingSubmit: {result}"
        # Must mention the stuck condition
        msg = result["message"].lower()
        assert "pendingsubmit" in msg or "stuck" in msg or "no permid" in msg or \
               "never confirmed" in msg or "pending" in msg, \
               f"Should mention stuck state: {result}"
