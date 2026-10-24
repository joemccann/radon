"""DS-2026-10-24-07: the order is built as the instrument the guards bounded.

Every guard reads ``type`` case-insensitively, while the contract dispatch in
``place_order`` compared it exactly and built a Stock for anything it did not
match. ``COMBO`` was therefore bounded as a defined-risk combo and transmitted
as a stock order.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import ib_place_order  # noqa: E402

OPTION = {"symbol": "AAPL", "action": "BUY", "quantity": 1, "limitPrice": 1.0,
          "expiry": "20261120", "strike": 200.0, "right": "C"}


def _place(params):
    client = MagicMock()
    client.qualify_contracts.return_value = []  # stop after the dispatch
    with patch("ib_place_order.IBClient", return_value=client), \
         patch("ib_place_order.Stock") as stock, \
         patch("clients.contract_resolver.resolve_option_contract", return_value=MagicMock()) as option:
        result = ib_place_order.place_order(params)
    return result, stock, option


@pytest.mark.parametrize("spelling", ["OPTION", "Option", " option "])
def test_a_differently_cased_type_is_built_as_that_instrument(spelling):
    result, stock, option = _place({**OPTION, "type": spelling})
    stock.assert_not_called()
    option.assert_called_once()
    assert result["status"] == "error"  # the stubbed qualification refused


def test_an_unknown_type_is_refused_before_ib():
    with patch("ib_place_order.IBClient") as client_cls:
        result = ib_place_order.place_order({**OPTION, "type": "bond"})
    assert result["status"] == "error"
    assert "type" in result["message"]
    client_cls.assert_not_called()


def test_a_missing_type_is_still_a_stock_order():
    params = {"symbol": "AAPL", "action": "BUY", "quantity": 1, "limitPrice": 1.0}
    result, stock, option = _place(params)
    stock.assert_called_once()
    option.assert_not_called()
