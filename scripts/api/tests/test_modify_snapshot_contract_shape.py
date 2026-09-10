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


# ---------------------------------------------------------------------------
# T-385: bind the REAL serializer to `_working_order_shape` — no hand-built
# snapshot rows. A `serialize_contract` key rename must red these.
# ---------------------------------------------------------------------------

from types import SimpleNamespace


class _FakeIBClient:
    """Duck-typed stand-in for `IBClient` — never touches a gateway."""

    def __init__(self, trades, resolved=None):
        self._trades = trades
        self._resolved = resolved or {}

    def get_open_orders(self):
        return self._trades

    def qualify_contracts(self, contract):
        resolved = self._resolved.get(contract.conId)
        return [resolved] if resolved else []


def _trade(contract, order_id=41, quantity=5_000.0, price=12.0, action="SELL"):
    return SimpleNamespace(
        contract=contract,
        order=SimpleNamespace(
            orderId=order_id, permId=991, orderRef="radon-abc", action=action,
            orderType="LMT", totalQuantity=quantity, lmtPrice=price,
            auxPrice=None, tif="DAY", outsideRth=False,
        ),
        orderStatus=SimpleNamespace(
            status="Submitted", filled=0.0, remaining=quantity, avgFillPrice=0.0
        ),
    )


def _stk_contract(symbol="SOFI"):
    return SimpleNamespace(
        conId=1, symbol=symbol, secType="STK", currency="USD", multiplier="",
        localSymbol=symbol, tradingClass=symbol, strike=0.0, right="",
        lastTradeDateOrContractMonth="", comboLegs=None,
    )


def _opt_leg_contract(con_id, strike, right):
    return SimpleNamespace(
        conId=con_id, symbol="SPY", secType="OPT", currency="USD",
        multiplier="100", localSymbol="", tradingClass="SPY", strike=strike,
        right=right, lastTradeDateOrContractMonth="20261218",
    )


def _bag_contract(leg_con_ids):
    return SimpleNamespace(
        conId=0, symbol="SPY", secType="BAG", currency="USD", multiplier="",
        localSymbol="", tradingClass="", strike=0.0, right="",
        lastTradeDateOrContractMonth="",
        comboLegs=[
            SimpleNamespace(conId=cid, ratio=1, action=act)
            for cid, act in leg_con_ids
        ],
    )


def _serialized_stock_row():
    from ib_orders import fetch_open_orders

    rows = fetch_open_orders(_FakeIBClient([_trade(_stk_contract())]))
    assert len(rows) == 1
    return rows[0]


class TestSerializerBindsToWorkingOrderShape:
    """Run `fetch_open_orders` for real; a serializer rename must red this."""

    def test_serialized_stk_row_measures_as_stock(self):
        from order_limits import _working_order_shape

        assert _working_order_shape(_serialized_stock_row()) == ("stock", None)

    def test_serialized_bag_row_measures_as_combo_with_legs(self):
        from ib_orders import fetch_open_orders
        from order_limits import _working_order_shape

        resolved = {
            11: _opt_leg_contract(11, 400.0, "P"),
            12: _opt_leg_contract(12, 410.0, "C"),
        }
        client = _FakeIBClient(
            [_trade(_bag_contract([(11, "SELL"), (12, "BUY")]),
                    order_id=42, quantity=1.0, price=1.0, action="BUY")],
            resolved,
        )
        rows = fetch_open_orders(client)
        shape, legs = _working_order_shape(rows[0])
        assert shape == "combo"
        assert legs == rows[0]["contract"]["comboLegs"]
        assert len(legs) == 2

    def test_stk_leg_exemption_matches_a_real_serialized_leg(self):
        """A covered combo (STK leg + OPT leg) must stay a priceable combo.

        `_legs_are_priceable` exempts STK legs from the strike requirement,
        but the serializer must actually EMIT `secType` on resolved legs for
        that exemption to ever match — a stock leg's strike is 0.
        """
        from ib_orders import fetch_open_orders
        from order_limits import _working_order_shape

        resolved = {
            21: _stk_contract("SPY"),
            22: _opt_leg_contract(22, 410.0, "C"),
        }
        resolved[21].conId = 21
        client = _FakeIBClient(
            [_trade(_bag_contract([(21, "BUY"), (22, "SELL")]),
                    order_id=43, quantity=1.0, price=1.0, action="BUY")],
            resolved,
        )
        rows = fetch_open_orders(client)
        shape, legs = _working_order_shape(rows[0])
        assert shape == "combo", (
            "an STK leg with strike 0 demoted a real covered combo to 'option'"
        )
        assert legs is not None


class TestModifyRouteWireWithSeededSnapshot:
    """POST /orders/modify against the REAL serialized snapshot row."""

    @staticmethod
    def _client(monkeypatch, tmp_path, snapshot_row):
        from unittest.mock import AsyncMock

        from fastapi.testclient import TestClient

        import trading_halt
        from scripts.api import auth, server

        monkeypatch.setattr(auth, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server, "is_trusted_local_request", lambda request: True)
        monkeypatch.setattr(server, "test_mode", False)
        monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
        monkeypatch.setattr(
            server,
            "_read_orders_snapshot_from_db",
            AsyncMock(return_value={"open_orders": [snapshot_row]}),
        )
        monkeypatch.setattr(server, "record_order_event", AsyncMock())
        spawn = AsyncMock(
            return_value=__import__("types").SimpleNamespace(
                ok=True, error=None,
                data={"status": "ok", "orderId": 41, "finalStatus": "Submitted"},
            )
        )
        monkeypatch.setattr(server, "_run_ib_script_with_recovery", spawn)
        return TestClient(server.app), spawn

    def test_ten_thousand_share_resize_spawns_the_modify(self, monkeypatch, tmp_path):
        client, spawn = self._client(monkeypatch, tmp_path, _serialized_stock_row())

        resp = client.post(
            "/orders/modify", json={"orderId": 41, "newQuantity": 10000}
        )
        assert resp.status_code == 200, resp.text
        spawn.assert_awaited_once_with(
            "ib_order_manage.py",
            ["modify", "--order-id", "41", "--new-quantity", "10000"],
            timeout=15,
        )

    def test_sixty_thousand_shares_is_422_and_never_spawns(self, monkeypatch, tmp_path):
        client, spawn = self._client(monkeypatch, tmp_path, _serialized_stock_row())

        resp = client.post(
            "/orders/modify", json={"orderId": 41, "newQuantity": 60000}
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["code"] == "ORDER_QTY_LIMIT"
        assert "RADON_MAX_STOCK_ORDER_QTY" in detail["message"]
        spawn.assert_not_awaited()
