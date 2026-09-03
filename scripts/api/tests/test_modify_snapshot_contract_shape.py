"""R-431 (P1): `/orders/modify` measured every working order as an OPTION.

`check_modify_limits` read `secType` and `legs` off the TOP level of the
working order, but the Turso `open_orders` payload
(`ib_orders.py:fetch_open_orders`) nests them at `contract.secType` and
`contract.comboLegs`. Every real snapshot row therefore resolved to
`"option"`, so:

  * a STOCK modify was bounded by the contract cap (`RADON_MAX_ORDER_QTY`,
    hard max 2500) instead of `RADON_MAX_STOCK_ORDER_QTY` — a 10,000-share
    resize was refused with "quantity 10000 exceeds the server-side limit of
    2500 (RADON_MAX_ORDER_QTY)", and raising the contract cap to its ceiling
    did not unblock it; and
  * a BAG never reached the combo max-loss branch R-145 added, because
    `combo_max_loss()` returns None for anything not typed "combo".

The existing R-145 tests passed a hand-built flat shape the snapshot never
emits, which is why neither hole was caught.
"""

from __future__ import annotations

import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent
for p in (str(REPO_ROOT), str(API_DIR.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _stock_order(quantity: float) -> dict:
    """A working stock order exactly as `fetch_open_orders` serializes it."""
    return {
        "orderId": 41,
        "permId": 991,
        "orderRef": "radon-abc",
        "symbol": "SOFI",
        "contract": {
            "conId": 1,
            "symbol": "SOFI",
            "secType": "STK",
            "currency": "USD",
            "multiplier": 1,
            "strike": 0.0,
            "right": "",
            "expiry": None,
        },
        "action": "SELL",
        "orderType": "LMT",
        "totalQuantity": quantity,
        "limitPrice": 12.0,
        "auxPrice": None,
        "status": "Submitted",
        "filled": 0.0,
        "remaining": quantity,
        "tif": "DAY",
        "outsideRth": False,
    }


class TestStockModifyUsesTheShareCap:
    def test_ten_thousand_shares_is_not_refused_by_the_contract_cap(self):
        from order_limits import check_modify_limits

        violation = check_modify_limits(
            _stock_order(5_000), new_quantity=10_000, new_price=12.0
        )
        assert violation is None, (
            "a 10,000-share stock modify was refused by the options contract cap"
        )

    def test_past_the_share_cap_is_still_refused(self):
        from order_limits import check_modify_limits

        violation = check_modify_limits(
            _stock_order(5_000), new_quantity=60_000, new_price=1.0
        )
        assert violation is not None
        assert violation["code"] == "ORDER_QTY_LIMIT"
        assert "RADON_MAX_STOCK_ORDER_QTY" in violation["message"]

    def test_a_stock_modify_still_hits_the_notional_cap(self):
        from order_limits import check_modify_limits

        violation = check_modify_limits(
            _stock_order(5_000), new_quantity=10_000, new_price=500.0
        )
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"


class TestSnapshotBagIsMeasuredAsACombo:
    def test_a_snapshot_bag_reaches_the_max_loss_branch(self):
        from order_limits import check_modify_limits

        working = {
            "orderId": 42,
            "permId": 992,
            "symbol": "SPY Spread",
            "contract": {
                "symbol": "SPY",
                "secType": "BAG",
                "comboLegs": [
                    {"conId": 11, "ratio": 1, "action": "SELL", "right": "P", "strike": 400.0},
                    {"conId": 12, "ratio": 1, "action": "BUY", "right": "C", "strike": 410.0},
                ],
            },
            "action": "BUY",
            "orderType": "LMT",
            "totalQuantity": 1.0,
            "limitPrice": 1.0,
        }
        violation = check_modify_limits(working, new_quantity=500, new_price=1.00)
        assert violation is not None, (
            "a snapshot-shaped BAG resized to 500 lots skipped the max-loss branch"
        )
        assert violation["code"] in {
            "ORDER_MAX_LOSS_LIMIT",
            "ORDER_NOTIONAL_LIMIT",
            "ORDER_EFFECTIVE_QTY_LIMIT",
        }

    def test_a_defined_risk_snapshot_bag_resize_still_passes(self):
        from order_limits import check_modify_limits

        working = {
            "contract": {
                "symbol": "SPY",
                "secType": "BAG",
                "comboLegs": [
                    {"conId": 11, "ratio": 1, "action": "SELL", "right": "P", "strike": 195.0},
                    {"conId": 12, "ratio": 1, "action": "BUY", "right": "P", "strike": 190.0},
                ],
            },
            "action": "BUY",
            "totalQuantity": 1.0,
            "limitPrice": 1.0,
        }
        assert check_modify_limits(working, new_quantity=500, new_price=1.00) is None

    def test_a_bag_with_unresolvable_legs_keeps_the_contract_cap(self):
        """`fetch_open_orders` skips legs it cannot qualify, so a strikeless
        BAG falls back to the bound it CAN compute rather than failing closed
        on a legitimate resize."""
        from order_limits import check_modify_limits

        working = {
            "contract": {
                "symbol": "SPY",
                "secType": "BAG",
                "comboLegs": [
                    {"conId": 11, "ratio": 1, "action": "SELL"},
                    {"conId": 12, "ratio": 1, "action": "BUY"},
                ],
            },
            "action": "BUY",
            "totalQuantity": 1.0,
            "limitPrice": 1.0,
        }
        assert check_modify_limits(working, new_quantity=10, new_price=1.00) is None
        refused = check_modify_limits(working, new_quantity=5_000, new_price=1.00)
        assert refused is not None
        assert refused["code"] == "ORDER_QTY_LIMIT"
