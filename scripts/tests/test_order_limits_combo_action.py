#!/usr/bin/env python3
"""Combo leg and envelope actions are allow-listed at the limit layer.

The risk math classifies a leg as short only when its action starts with
SELL, while the placer forwards the raw action string to IB. Any other
value was priced as a long leg, so a combo's max loss could be understated.
Only BUY and SELL may reach the limit math; anything else refuses.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import order_limits


def _put_spread(short_action: str = "SELL", envelope: str = "BUY") -> dict:
    return {
        "type": "combo", "symbol": "SPY", "action": envelope,
        "quantity": 1, "limitPrice": 1.0,
        "legs": [
            {"expiry": "20261218", "strike": 100, "right": "P", "action": short_action, "ratio": 1},
            {"expiry": "20261218", "strike": 90, "right": "P", "action": "BUY", "ratio": 1},
        ],
    }


@pytest.mark.parametrize("action", ["SSHORT", "SLONG", 1, "HOLD"])
def test_unknown_leg_action_is_refused(action):
    violation = order_limits.check_order_limits(_put_spread(short_action=action))
    assert violation is not None
    assert violation["code"] == "ORDER_COMBO_ACTION"


@pytest.mark.parametrize("envelope", ["SSHORT", "HOLD", 1])
def test_unknown_envelope_action_is_refused(envelope):
    violation = order_limits.check_order_limits(_put_spread(envelope=envelope))
    assert violation is not None
    assert violation["code"] == "ORDER_COMBO_ACTION"


@pytest.mark.parametrize("short_action", ["SELL", "sell", " Sell "])
def test_buy_sell_legs_still_pass(short_action):
    assert order_limits.check_order_limits(_put_spread(short_action=short_action)) is None


def test_absent_leg_action_keeps_its_existing_meaning():
    params = _put_spread()
    del params["legs"][0]["action"]
    del params["action"]
    violation = order_limits.check_order_limits(params)
    assert violation is None or violation["code"] != "ORDER_COMBO_ACTION"


def test_snapshot_with_unknown_leg_action_falls_back_to_option_bounds():
    """A resize of an order IB already holds keeps the option bounds instead
    of pricing an unknown leg as long or refusing a legitimate modify."""
    working = {
        "action": "BUY", "quantity": 1, "limitPrice": 1.0,
        "contract": {"secType": "BAG", "comboLegs": _put_spread(short_action="SSHORT")["legs"]},
    }
    assert order_limits._working_order_shape(working)[0] == "option"
