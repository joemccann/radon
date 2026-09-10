"""REL-211 (R-580, R-581): a stock modify with no resolvable price fails the
notional check closed, and the STK+legs classification is pinned."""
from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from order_limits import check_modify_limits  # noqa: E402


def _stock_order(**over):
    base = {
        "contract": {"secType": "STK", "symbol": "NVDA"},
        "quantity": 100,
        "action": "BUY",
    }
    base.update(over)
    return base


class TestPricelessStockModifyFailsClosed:
    def test_a_priceless_stock_order_at_9000_shares_is_refused(self):
        """R-580: price resolved to 0, order_notional returned None, and the
        notional check was skipped — 9,000 x a $500 stock is $4.5M past the
        $250k cap, bounded only by the 50,000-share band."""
        violation = check_modify_limits(_stock_order(), new_quantity=9000)
        assert violation is not None, (
            "a quantity-only modify of a priceless stock order bypassed the "
            "notional cap entirely"
        )
        assert "price" in violation["message"].lower() or "notional" in violation[
            "message"
        ].lower()

    def test_a_stop_order_resolves_its_aux_price(self):
        """A working STP carries auxPrice — that bounds the notional, so a
        legitimate small stop-order modify is not refused."""
        violation = check_modify_limits(
            _stock_order(auxPrice=50.0), new_quantity=100
        )
        assert violation is None

    def test_an_over_notional_stop_modify_is_refused_via_aux_price(self):
        violation = check_modify_limits(
            _stock_order(auxPrice=500.0), new_quantity=9000
        )
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_a_limit_priced_stock_modify_is_unchanged(self):
        assert check_modify_limits(
            _stock_order(limitPrice=100.0), new_quantity=100
        ) is None


class TestStkWithLegsClassification:
    def test_a_stk_row_carrying_legs_is_bounded_as_a_combo_shape(self):
        """R-581 pin: combo classification wins over STK. This fails CLOSED
        (stricter caps), and no current snapshot emits the shape — pinned so
        a reorder that silently flips it to the looser stock path goes red."""
        order = _stock_order(
            legs=[
                {"secType": "OPT", "strike": 100, "right": "C", "action": "SELL", "ratio": 1},
                {"secType": "OPT", "strike": 90, "right": "P", "action": "BUY", "ratio": 1},
            ],
            limitPrice=1.0,
        )
        from order_limits import _working_order_shape

        order_type, legs = _working_order_shape(order)
        assert order_type in ("combo", "option")
        assert order_type != "stock"


def _option_order(**over):
    base = {
        "contract": {"secType": "OPT", "symbol": "NVDA", "strike": 200, "right": "C"},
        "quantity": 1,
        "action": "BUY",
    }
    base.update(over)
    return base


class TestPricelessOptionModifyAlsoFailsClosed:
    """R-632: the fail-closed refusal was keyed on `order_type == 'stock'`,
    so a priceless OPT fell through with `limitPrice: 0`, never had its
    notional computed, and was bounded solely by the 500-contract band."""

    def test_a_priceless_option_modify_under_the_qty_band_is_refused(self):
        violation = check_modify_limits(_option_order(), new_quantity=400)
        assert violation is not None, (
            "a priceless OPT modify at 400 contracts passed every check"
        )
        assert "price" in violation["message"].lower()

    def test_a_priced_option_modify_is_unchanged(self):
        assert check_modify_limits(_option_order(limitPrice=2.5), new_quantity=4) is None

    def test_a_priceless_combo_is_still_bounded_by_its_legs_not_refused(self):
        """A combo's exposure comes from `combo_max_loss()` over its legs,
        not from a premium, so the widened refusal must not swallow it."""
        combo = {
            "contract": {"secType": "BAG", "symbol": "NVDA"},
            "quantity": 1,
            "action": "BUY",
            "legs": [
                {"strike": 200, "right": "P", "action": "SELL", "ratio": 1},
                {"strike": 190, "right": "P", "action": "BUY", "ratio": 1},
            ],
        }
        violation = check_modify_limits(combo, new_quantity=1)
        assert violation is None or "price" not in violation["message"].lower(), violation
