"""REL-329 / R-740 — a fresh close of a held long combo is not new short risk.

A SELL envelope reverses every leg at IB. `combo_max_loss` therefore prices
BUY legs as short assignment, which is the opening-short hole closed on
2026-10-20. The same bytes are how a long strangle is closed (structure
legs unchanged, envelope SELL). Ten SPX puts at 6000 and 5000 price at
$10,999,000 against the $10,000,000 fat-finger cap, so `/orders/place`
refuses the close before the bankroll close-out exemption can see it.

The exemption is the snapshot, not a client flag: the book must be inside
the 15-minute window and every leg must only reduce a held leg. An empty,
stale, or short book keeps the refusal. The quantity cap still refuses.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException

_SCRIPTS = Path(__file__).resolve().parent.parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bankroll_guard
import ib_place_order
import order_limits

NOW = datetime(2026, 10, 21, 15, 0, tzinfo=timezone.utc)


def _close_long_strangle(quantity: int = 10) -> dict:
    return {
        "type": "combo",
        "symbol": "SPX",
        "action": "SELL",
        "quantity": quantity,
        "limitPrice": 1.0,
        "legs": [
            {"expiry": "20261218", "strike": 6000.0, "right": "P", "action": "BUY", "ratio": 1},
            {"expiry": "20261218", "strike": 5000.0, "right": "P", "action": "BUY", "ratio": 1},
        ],
    }


def _held_long_strangle(contracts: int = 10, *, age_min: float = 1.0) -> dict:
    return {
        "last_sync": (NOW - timedelta(minutes=age_min)).isoformat(),
        "positions": [{
            "ticker": "SPX",
            "legs": [
                {"type": "Put", "strike": 6000.0, "direction": "LONG", "contracts": contracts, "expiry": "20261218"},
                {"type": "Put", "strike": 5000.0, "direction": "LONG", "contracts": contracts, "expiry": "20261218"},
            ],
        }],
    }


@pytest.fixture
def pinned_cap(monkeypatch):
    monkeypatch.setattr(order_limits, "max_combo_loss_dollars", lambda: 10_000_000.0)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(bankroll_guard, "_utcnow", lambda: NOW)


def _placement(params: dict):
    """Run the placement funnel up to the broker socket."""
    with patch("ib_place_order.IBClient") as ib_client_cls:
        ib_client_cls.return_value.connect.side_effect = RuntimeError("injected-no-broker")
        result = ib_place_order.place_order(params)
        return result, ib_client_cls


class TestLossCapVersusFreshClose:
    def test_unheld_sell_envelope_is_still_refused(self, pinned_cap, frozen_clock, monkeypatch):
        """The opening-short hole stays closed when the book does not hold the legs."""
        monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: _held_long_strangle(0))
        loss = order_limits.combo_max_loss(_close_long_strangle())
        assert loss == pytest.approx(10_999_000.0)
        violation = order_limits.check_placement_limits(_close_long_strangle())
        assert violation is not None
        assert violation["code"] == "ORDER_MAX_LOSS_LIMIT"
        result, ib_client_cls = _placement(_close_long_strangle())
        assert result["status"] == "error"
        assert "max loss" in result["message"].lower()
        ib_client_cls.assert_not_called()

    def test_fresh_close_of_the_held_long_is_admitted(self, pinned_cap, frozen_clock, monkeypatch):
        """Ten held SPX puts may be sold. The broker socket is the proof the cap let go."""
        monkeypatch.setattr(
            bankroll_guard, "_load_latest_snapshot", lambda: _held_long_strangle(10),
        )
        assert order_limits.check_placement_limits(_close_long_strangle()) is None
        result, ib_client_cls = _placement(_close_long_strangle())
        assert "max loss" not in result.get("message", "").lower()
        assert result["message"] == "Connection failed: injected-no-broker"
        ib_client_cls.assert_called_once()

    def test_stale_book_does_not_exempt_the_close(self, pinned_cap, frozen_clock, monkeypatch):
        monkeypatch.setattr(
            bankroll_guard, "_load_latest_snapshot",
            lambda: _held_long_strangle(10, age_min=16),
        )
        violation = order_limits.check_placement_limits(_close_long_strangle())
        assert violation is not None
        assert violation["code"] == "ORDER_MAX_LOSS_LIMIT"
        result, ib_client_cls = _placement(_close_long_strangle())
        assert "max loss" in result["message"].lower()
        ib_client_cls.assert_not_called()

    def test_snapshot_load_failure_keeps_the_refusal(self, pinned_cap, frozen_clock, monkeypatch):
        def _boom():
            raise RuntimeError("snapshot store down")

        monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", _boom)
        violation = order_limits.check_placement_limits(_close_long_strangle())
        assert violation is not None
        assert violation["code"] == "ORDER_MAX_LOSS_LIMIT"

    def test_oversell_and_quantity_cap_still_refuse(self, pinned_cap, frozen_clock, monkeypatch):
        monkeypatch.setattr(
            bankroll_guard, "_load_latest_snapshot", lambda: _held_long_strangle(10),
        )
        oversell = order_limits.check_placement_limits(_close_long_strangle(11))
        assert oversell is not None
        assert oversell["code"] == "ORDER_MAX_LOSS_LIMIT"
        too_many = order_limits.check_placement_limits(_close_long_strangle(501))
        assert too_many is not None
        assert too_many["code"] == "ORDER_QTY_LIMIT"

    def test_route_refusal_uses_the_same_exemption(self, pinned_cap, frozen_clock, monkeypatch):
        from scripts.api import server

        monkeypatch.setattr(
            bankroll_guard, "_load_latest_snapshot", lambda: _held_long_strangle(10),
        )
        server._refuse_if_order_limits_violated(_close_long_strangle())

        monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: _held_long_strangle(0))
        with pytest.raises(HTTPException) as raised:
            server._refuse_if_order_limits_violated(_close_long_strangle())
        assert raised.value.status_code == 422
        assert raised.value.detail["code"] == "ORDER_MAX_LOSS_LIMIT"
