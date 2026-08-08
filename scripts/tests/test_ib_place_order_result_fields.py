"""T-032 — partial-fill surfacing in ib_place_order.py's result dicts.

Two gaps:
  (a) A partially-filled-then-Cancelled order's error return (the terminal-
      failed branch, scripts/ib_place_order.py:452-458 / _build_terminal_error)
      drops the fill entirely — the caller can't tell "40 of 100 filled before
      IB cancelled the rest" from "nothing happened".
  (b) A successfully-submitted order's ok return (:460-466) never surfaces
      filled/remaining/avgFillPrice even when IB reports them, so the UI has
      no way to show partial execution on an order that is still open.

Mirrors test_spx01_grace_wait.py's fixture style (_make_order /
_make_order_status / _make_trade / _make_client / triple-patch of
IBClient+Stock+LimitOrder), extended with filled/remaining/avgFillPrice on
the mocked OrderStatus since that's exactly what's under test here.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import eventkit

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Helpers — mirrors test_spx01_grace_wait.py's fixture style, extended with
# filled/remaining/avgFillPrice on the mocked OrderStatus.
# ---------------------------------------------------------------------------

def _make_order(order_id: int = 99, perm_id: int = 12345) -> MagicMock:
    o = MagicMock()
    o.orderId = order_id
    o.permId = perm_id
    return o


def _make_order_status(status: str, filled: float = 0, remaining: float = 0,
                       avg_fill_price: float = 0.0) -> MagicMock:
    os = MagicMock()
    os.status = status
    os.whyHeld = ""
    os.filled = filled
    os.remaining = remaining
    os.avgFillPrice = avg_fill_price
    return os


def _make_trade(status: str = "Submitted", perm_id: int = 12345,
                filled: float = 0, remaining: float = 0,
                avg_fill_price: float = 0.0) -> MagicMock:
    trade = MagicMock()
    trade.order = _make_order(order_id=99, perm_id=perm_id)
    trade.orderStatus = _make_order_status(
        status=status, filled=filled, remaining=remaining,
        avg_fill_price=avg_fill_price,
    )
    trade.log = []
    return trade


def _make_client(trade: MagicMock) -> MagicMock:
    client = MagicMock()

    real_error_event = eventkit.Event("errorEvent")
    ib_mock = MagicMock()
    ib_mock.errorEvent = real_error_event
    client._ib = ib_mock

    client.place_order = MagicMock(return_value=trade)
    client.qualify_contracts = MagicMock(return_value=[MagicMock(conId=123456)])
    client.sleep = MagicMock()
    client.disconnect = MagicMock()
    return client


def _invoke_place_order(params: dict, client_mock: MagicMock) -> dict:
    with patch("ib_place_order.IBClient", return_value=client_mock), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        import ib_place_order
        return ib_place_order.place_order(params)


_STOCK_PARAMS = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 100,
    "limitPrice": 214.50,
    "tif": "DAY",
}


# ---------------------------------------------------------------------------
# (a) partial-fill-then-Cancelled must surface on the error return
# ---------------------------------------------------------------------------

class TestPartialFillSurfacedOnError:
    def test_partial_fill_then_cancelled_includes_filled_and_avg_price(self):
        trade = _make_trade(status="Cancelled", perm_id=12345,
                            filled=40, remaining=60, avg_fill_price=123.45)
        client = _make_client(trade)

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "error", f"expected error, got: {result}"
        assert result["filled"] == 40, f"missing/incorrect filled qty: {result}"
        assert result["avg_fill_price"] == 123.45, f"missing/incorrect avg_fill_price: {result}"

    def test_partial_fill_mentioned_in_message(self):
        trade = _make_trade(status="Cancelled", perm_id=12345,
                            filled=40, remaining=60, avg_fill_price=123.45)
        client = _make_client(trade)

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert "40" in result["message"], f"filled qty not mentioned in message: {result}"

    def test_full_reject_no_fill_omits_fill_fields(self):
        """Regression guard: an outright reject (nothing filled) must not
        claim a partial fill happened. NOTE: this passes even before the
        T-032 fix — the unfixed code never adds these keys at all, filled or
        not. It stays green after the fix by construction: filled=0 is
        falsy, so _build_terminal_error's `if filled:` guard omits the keys.
        Kept as a regression pin against a future change that adds them
        unconditionally.
        """
        trade = _make_trade(status="Rejected", perm_id=12345,
                            filled=0, remaining=100, avg_fill_price=0.0)
        client = _make_client(trade)

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "error"
        assert "filled" not in result, f"zero-fill reject should omit filled: {result}"
        assert "avg_fill_price" not in result, f"zero-fill reject should omit avg_fill_price: {result}"


# ---------------------------------------------------------------------------
# (b) fill fields must surface on the ok return when present
# ---------------------------------------------------------------------------

class TestFillFieldsSurfacedOnOk:
    def test_ok_return_includes_filled_remaining_avg_fill_price(self):
        trade = _make_trade(status="Submitted", perm_id=12345,
                            filled=25, remaining=75, avg_fill_price=99.10)
        client = _make_client(trade)

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "ok", f"expected ok, got: {result}"
        assert result["filled"] == 25, f"missing/incorrect filled: {result}"
        assert result["remaining"] == 75, f"missing/incorrect remaining: {result}"
        assert result["avgFillPrice"] == 99.10, f"missing/incorrect avgFillPrice: {result}"

    def test_ok_return_omits_fill_fields_when_nothing_filled(self):
        """Regression guard, same caveat as test_full_reject_no_fill_omits_fill_fields
        above: already true pre-fix (the keys never existed at all), stays
        true post-fix because filled=0 is falsy.
        """
        trade = _make_trade(status="Submitted", perm_id=12345,
                            filled=0, remaining=100, avg_fill_price=0.0)
        client = _make_client(trade)

        result = _invoke_place_order(_STOCK_PARAMS, client)

        assert result["status"] == "ok"
        assert "filled" not in result, f"zero-fill ok should omit filled: {result}"
        assert "remaining" not in result, f"zero-fill ok should omit remaining: {result}"
        assert "avgFillPrice" not in result, f"zero-fill ok should omit avgFillPrice: {result}"
