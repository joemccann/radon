"""T-030 — LimitOrder kwargs contract for ib_place_order.py.

place_order() must construct the IB LimitOrder with action / totalQuantity /
lmtPrice / tif / outsideRth taken VERBATIM from params (after the module's
own normalization: symbol/action upper(), quantity int(), limitPrice
float(), tif upper()/default, outsideRth bool()) — no swapping, no silent
substitution of one field's value for another.

These tests patch LimitOrder (mirroring test_spx01_grace_wait.py's harness:
same _make_order / _make_order_status / _make_trade / _make_client shapes,
same triple-patch of IBClient/Stock/LimitOrder) and assert directly on the
constructor's call kwargs, rather than inferring correctness indirectly from
the returned result dict.

TEST-ONLY — scripts/ib_place_order.py:314-320 already satisfies this
contract; there is no fix diff for T-030. See NOTES.md for how to
demonstrate RED by temporarily mutating the source.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import eventkit

# Ensure scripts/ is importable
_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


# ---------------------------------------------------------------------------
# Helpers — mirrors test_spx01_grace_wait.py's fixture style
# ---------------------------------------------------------------------------

def _make_order(order_id: int = 99, perm_id: int = 12345) -> MagicMock:
    o = MagicMock()
    o.orderId = order_id
    o.permId = perm_id
    return o


def _make_order_status(status: str) -> MagicMock:
    os = MagicMock()
    os.status = status
    os.whyHeld = ""
    return os


def _make_trade(status: str = "Submitted", perm_id: int = 12345) -> MagicMock:
    trade = MagicMock()
    trade.order = _make_order(order_id=99, perm_id=perm_id)
    trade.orderStatus = _make_order_status(status=status)
    trade.log = []
    return trade


def _make_client(trade: MagicMock) -> MagicMock:
    """Mock IBClient whose trade is already Submitted+permId!=0, so the
    confirm-poll loop breaks on its first iteration — these tests only care
    about the LimitOrder call, not about polling behaviour.
    """
    client = MagicMock()

    # Use a real eventkit.Event so the `+=` operator in ib_place_order.py
    # registers the error callback correctly (same trick as test_spx01_grace_wait.py).
    real_error_event = eventkit.Event("errorEvent")
    ib_mock = MagicMock()
    ib_mock.errorEvent = real_error_event
    client._ib = ib_mock

    client.place_order = MagicMock(return_value=trade)
    client.qualify_contracts = MagicMock(return_value=[MagicMock(conId=123456)])
    client.sleep = MagicMock()
    client.disconnect = MagicMock()
    return client


def _invoke_place_order(params: dict, client_mock: MagicMock):
    """Call place_order() with IBClient/Stock/LimitOrder patched.

    Returns (result, limit_order_cls) so callers can assert on the
    LimitOrder constructor's call kwargs via limit_order_cls.call_args.
    """
    with patch("ib_place_order.IBClient", return_value=client_mock), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()) as limit_order_cls:
        import ib_place_order
        result = ib_place_order.place_order(params)
        return result, limit_order_cls


_BASE_PARAMS = {
    "type": "stock",
    "symbol": "AAPL",
    "action": "BUY",
    "quantity": 100,
    "limitPrice": 214.50,
    "tif": "DAY",
    "outsideRth": False,
}


# ---------------------------------------------------------------------------
# T-030 Tests
# ---------------------------------------------------------------------------

class TestLimitOrderKwargsContract:
    def test_action_quantity_price_pass_through_verbatim(self):
        client = _make_client(_make_trade())
        params = dict(_BASE_PARAMS, action="BUY", quantity=100, limitPrice=214.50)

        result, limit_order_cls = _invoke_place_order(params, client)

        assert result["status"] == "ok", f"fixture setup should succeed: {result}"
        limit_order_cls.assert_called_once()
        kwargs = limit_order_cls.call_args.kwargs
        assert kwargs["action"] == "BUY", f"action not verbatim: {kwargs}"
        assert kwargs["totalQuantity"] == 100, f"totalQuantity not verbatim: {kwargs}"
        assert kwargs["lmtPrice"] == 214.50, f"lmtPrice not verbatim: {kwargs}"

    def test_sell_action_passed_verbatim(self):
        client = _make_client(_make_trade())
        params = dict(_BASE_PARAMS, action="SELL")

        _, limit_order_cls = _invoke_place_order(params, client)

        kwargs = limit_order_cls.call_args.kwargs
        assert kwargs["action"] == "SELL", f"action not verbatim: {kwargs}"

    def test_outside_rth_true_passed_verbatim(self):
        client = _make_client(_make_trade())
        params = dict(_BASE_PARAMS, outsideRth=True)

        _, limit_order_cls = _invoke_place_order(params, client)

        kwargs = limit_order_cls.call_args.kwargs
        assert kwargs["outsideRth"] is True, f"outsideRth=True not verbatim: {kwargs}"

    def test_outside_rth_false_passed_verbatim(self):
        client = _make_client(_make_trade())
        params = dict(_BASE_PARAMS, outsideRth=False)

        _, limit_order_cls = _invoke_place_order(params, client)

        kwargs = limit_order_cls.call_args.kwargs
        assert kwargs["outsideRth"] is False, f"outsideRth=False not verbatim: {kwargs}"

    def test_tif_gtc_passed_verbatim(self):
        client = _make_client(_make_trade())
        params = dict(_BASE_PARAMS, tif="GTC")

        _, limit_order_cls = _invoke_place_order(params, client)

        kwargs = limit_order_cls.call_args.kwargs
        assert kwargs["tif"] == "GTC", f"tif=GTC not verbatim: {kwargs}"

    def test_tif_day_passed_verbatim(self):
        client = _make_client(_make_trade())
        params = dict(_BASE_PARAMS, tif="DAY")

        _, limit_order_cls = _invoke_place_order(params, client)

        kwargs = limit_order_cls.call_args.kwargs
        assert kwargs["tif"] == "DAY", f"tif=DAY not verbatim: {kwargs}"
