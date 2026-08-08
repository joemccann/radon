"""T-034 — input bounds on ib_place_order.py's place path.

Mirrors the modify-side guard in scripts/ib_order_manage.py:189-192
(`new_price <= 0` / `new_quantity <= 0` refused with an operator-readable
message) for the PLACE path. Three gaps in the params-parsing region
(scripts/ib_place_order.py:174-185):
  * quantity <= 0 is never checked
  * a non-integral quantity is silently truncated by int() (10.5 -> 10)
    instead of refused
  * limitPrice <= 0 is never checked

All three must be refused with status:"error" and an operator-readable
message BEFORE any IBClient interaction — not just before qualify/place, but
before the client is even constructed (scripts/ib_place_order.py:187 sits
AFTER the params-parsing region, so a clean early `return` there means
IBClient() is never instantiated).

These tests patch IBClient itself (not just its return_value) so
`ib_client_cls.assert_not_called()` proves validation ran before ANY IB
interaction — connect, qualify_contracts, and place_order included.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _invoke_place_order(params: dict):
    """Call place_order() with IBClient/Stock/LimitOrder patched (mirrors
    test_spx01_grace_wait.py's harness).

    Returns (result, ib_client_cls) — ib_client_cls is the patched IBClient
    CLASS itself (not a pre-built instance), so callers can assert it was
    never even constructed.
    """
    with patch("ib_place_order.IBClient") as ib_client_cls, \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        import ib_place_order
        result = ib_place_order.place_order(params)
        return result, ib_client_cls


_BASE_PARAMS = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 100,
    "limitPrice": 214.50,
    "tif": "DAY",
}


class TestQuantityBounds:
    def test_zero_quantity_refused(self):
        params = dict(_BASE_PARAMS, quantity=0)

        result, ib_client_cls = _invoke_place_order(params)

        assert result["status"] == "error", f"expected error, got: {result}"
        assert "quantity" in result["message"].lower(), f"message not operator-readable: {result}"
        ib_client_cls.assert_not_called()

    def test_negative_quantity_refused(self):
        params = dict(_BASE_PARAMS, quantity=-10)

        result, ib_client_cls = _invoke_place_order(params)

        assert result["status"] == "error", f"expected error, got: {result}"
        assert "quantity" in result["message"].lower(), f"message not operator-readable: {result}"
        ib_client_cls.assert_not_called()

    def test_non_integral_quantity_refused(self):
        """int() truncates silently (10.5 -> 10) — must be refused outright,
        not rounded into placing a different-sized order than requested."""
        params = dict(_BASE_PARAMS, quantity=10.5)

        result, ib_client_cls = _invoke_place_order(params)

        assert result["status"] == "error", f"expected error, got: {result}"
        assert "quantity" in result["message"].lower(), f"message not operator-readable: {result}"
        ib_client_cls.assert_not_called()

    def test_integral_float_quantity_still_accepted(self):
        """100.0 IS a whole number even though it's a float — must NOT be
        refused by the non-integral check. Regression guard against an
        overly-strict isinstance(quantity, int) style check that would
        reject every caller whose JSON serializer emits whole-number
        quantities as floats."""
        params = dict(_BASE_PARAMS, quantity=100.0)

        result, ib_client_cls = _invoke_place_order(params)

        assert not (result.get("status") == "error" and "quantity" in result.get("message", "").lower()), (
            f"a whole-number float quantity must not be refused as non-integral: {result}"
        )
        ib_client_cls.assert_called_once()


class TestLimitPriceBounds:
    def test_zero_limit_price_refused(self):
        params = dict(_BASE_PARAMS, limitPrice=0)

        result, ib_client_cls = _invoke_place_order(params)

        assert result["status"] == "error", f"expected error, got: {result}"
        assert "limit price" in result["message"].lower() or "limitprice" in result["message"].lower(), \
            f"message not operator-readable: {result}"
        ib_client_cls.assert_not_called()

    def test_negative_limit_price_refused(self):
        params = dict(_BASE_PARAMS, limitPrice=-5.0)

        result, ib_client_cls = _invoke_place_order(params)

        assert result["status"] == "error", f"expected error, got: {result}"
        assert "limit price" in result["message"].lower() or "limitprice" in result["message"].lower(), \
            f"message not operator-readable: {result}"
        ib_client_cls.assert_not_called()
