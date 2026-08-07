"""IB what-if margin preview.

`place_order(params, what_if=True)` must hit IB's pre-trade risk engine via
`whatIfOrder` (no transmit), NEVER place the order, and shape the returned
`OrderState` into the margin JSON contract. The `_margin` parser nulls IB's
empty/sentinel margin fields so the UI shows "unavailable" instead of 0 or a
1.79e308 garbage number.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import eventkit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ib_place_order import _margin, _whatif_result  # noqa: E402


def _make_state(**kw) -> MagicMock:
    state = MagicMock()
    for key, value in kw.items():
        setattr(state, key, value)
    return state


def _make_client(state: MagicMock) -> MagicMock:
    client = MagicMock()
    ib_mock = MagicMock()
    ib_mock.errorEvent = eventkit.Event("errorEvent")  # real Event so += works
    client._ib = ib_mock
    client.qualify_contracts = MagicMock(return_value=[MagicMock(conId=1)])
    client.what_if_order = MagicMock(return_value=state)
    client.place_order = MagicMock()
    client.disconnect = MagicMock()
    return client


def _make_submitted_trade() -> MagicMock:
    trade = MagicMock()
    trade.order.orderId = 42
    trade.order.permId = 420042
    trade.orderStatus.status = "Submitted"
    trade.log = []
    return trade


_STOCK_PARAMS = {
    "type": "stock",
    "symbol": "TSLA",
    "action": "SELL",
    "quantity": 100,
    "limitPrice": 250.0,
    "tif": "DAY",
}


def _invoke(params: dict, client: MagicMock, what_if: bool = True) -> dict:
    with patch("ib_place_order.IBClient", return_value=client), \
         patch("ib_place_order.Stock", return_value=MagicMock()), \
         patch("ib_place_order.LimitOrder", return_value=MagicMock()):
        import ib_place_order
        return ib_place_order.place_order(params, what_if=what_if)


# ── pure margin parser ───────────────────────────────────────────────────────

def test_margin_parses_string():
    assert _margin("4250.0") == 4250.0
    assert _margin(4250.0) == 4250.0


def test_margin_empty_and_sentinel_are_none():
    assert _margin("") is None
    assert _margin(None) is None
    assert _margin("1.7976931348623157e+308") is None
    assert _margin(1.7976931348623157e+308) is None
    assert _margin("not-a-number") is None


def test_whatif_result_prefers_reported_change():
    state = _make_state(
        initMarginChange="4250.0",
        maintMarginChange="4250.0",
        equityWithLoanChange="-4250.0",
        commission="2.6",
        commissionCurrency="USD",
        warningText="",
    )
    out = _whatif_result(state)
    assert out["status"] == "ok"
    assert out["whatIf"] is True
    assert out["initMargin"] == 4250.0
    assert out["maintMargin"] == 4250.0
    assert out["source"] == "ib"
    assert out["warning"] is None  # empty warningText collapses to None


def test_whatif_result_after_minus_before_fallback():
    state = _make_state(
        initMarginChange="",
        initMarginBefore="1000.0",
        initMarginAfter="6000.0",
    )
    assert _whatif_result(state)["initMargin"] == 5000.0


def test_whatif_result_withheld_margin_is_none():
    # Off-hours / withheld: change AND before/after all come back sentinel/empty.
    state = _make_state(
        initMarginChange="1.7976931348623157e+308",
        initMarginBefore="",
        initMarginAfter="",
    )
    assert _whatif_result(state)["initMargin"] is None


# ── place_order what_if branch ───────────────────────────────────────────────

def test_what_if_calls_whatif_never_places():
    state = _make_state(
        initMarginChange="3300.0",
        maintMarginChange="3300.0",
        commission="1.5",
        commissionCurrency="USD",
        warningText="",
    )
    client = _make_client(state)
    result = _invoke(_STOCK_PARAMS, client, what_if=True)
    assert result["initMargin"] == 3300.0
    assert result["source"] == "ib"
    client.what_if_order.assert_called_once()
    client.place_order.assert_not_called()  # never routes an order
    client.disconnect.assert_called_once()  # finally still cleans up


def test_what_if_timeout_returns_error_not_place():
    client = _make_client(_make_state())
    client.what_if_order = MagicMock(side_effect=asyncio.TimeoutError())
    result = _invoke(_STOCK_PARAMS, client, what_if=True)
    assert result["status"] == "error"
    client.place_order.assert_not_called()


def test_live_place_routes_without_requesting_whatif():
    client = _make_client(_make_state())
    client.place_order.return_value = _make_submitted_trade()

    result = _invoke(_STOCK_PARAMS, client, what_if=False)

    assert result["status"] == "ok"
    client.place_order.assert_called_once()
    client.what_if_order.assert_not_called()


def test_smart_error_360_preview_cannot_poison_later_live_place():
    client = _make_client(_make_state())
    client.what_if_order.side_effect = RuntimeError(
        "IB error 360: No what-if check support for smart combo order"
    )
    client.place_order.return_value = _make_submitted_trade()

    preview = _invoke(_STOCK_PARAMS, client, what_if=True)
    assert preview["status"] == "error"
    assert "360" in preview["message"]
    client.place_order.assert_not_called()

    client.what_if_order.reset_mock()
    live = _invoke(_STOCK_PARAMS, client, what_if=False)

    assert live["status"] == "ok"
    client.place_order.assert_called_once()
    client.what_if_order.assert_not_called()


def test_live_place_never_runs_whatif_or_returns_projected_margin():
    client = _make_client(_make_state(initMarginChange="3300.0"))
    trade = MagicMock()
    trade.order.orderId = 101
    trade.order.permId = 202
    trade.orderStatus.status = "Submitted"
    trade.orderStatus.whyHeld = ""
    trade.log = []
    client.place_order.return_value = trade

    result = _invoke(_STOCK_PARAMS, client, what_if=False)

    assert result["status"] == "ok"
    assert "initMargin" not in result
    assert "maintMargin" not in result
    assert "marginSource" not in result
    client.what_if_order.assert_not_called()
    client.place_order.assert_called_once()
