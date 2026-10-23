"""DS-2026-10-23-03: the close exemption must account for resting closes.

``check_placement_limits`` waives the combo loss cap, and
``check_bankroll_admission`` waives the 2.5% position cap, whenever the order
only reduces a holding the latest snapshot shows. ``is_close_out`` read the
snapshot's held quantities alone, so every one of several closes of the same
holding was individually "only reducing". While the first close rests
unfilled the snapshot correctly still shows the whole position, which is
exactly the window in which both pass. Once more than one fills the surplus
opens a short whose assignment exposure is the number the waived cap exists
to refuse.

Working closing orders for the same contract are deducted before the
exemption is granted, and an unreadable working-order book keeps the refusal.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import bankroll_guard  # noqa: E402
import order_limits  # noqa: E402
from bankroll_guard import check_bankroll_admission as _real_bankroll_admission  # noqa: E402

NOW = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)

HELD_TEN_LONG_PUTS = {
    "ticker": "SPX",
    "expiry": "2026-11-20",
    "legs": [{"type": "Put", "strike": 5000.0, "direction": "LONG", "contracts": 10}],
}


def _snapshot(age_min=1.0, positions=None, nlv=100_000.0):
    return {
        "last_sync": (NOW - timedelta(minutes=age_min)).isoformat(),
        "account_summary": {"net_liquidation": nlv},
        "positions": positions if positions is not None else [HELD_TEN_LONG_PUTS],
    }


def _close(quantity=10):
    """Sell ten held SPX puts to close — priced as new short assignment."""
    return {
        "type": "option",
        "symbol": "SPX",
        "action": "SELL",
        "quantity": quantity,
        "limitPrice": 40.0,
        "expiry": "20261120",
        "strike": 5000.0,
        "right": "P",
    }


def _resting_close(quantity=10, remaining=None, status="Submitted"):
    """The Turso ``open_orders`` payload shape for the same close."""
    return {
        "permId": 900100,
        "action": "SELL",
        "orderType": "LMT",
        "status": status,
        "totalQuantity": float(quantity),
        "filled": 0.0,
        "remaining": float(quantity if remaining is None else remaining),
        "limitPrice": 40.0,
        "symbol": "SPX 20261120 5000 P",
        "contract": {
            "symbol": "SPX",
            "secType": "OPT",
            "strike": 5000.0,
            "right": "P",
            "expiry": "2026-11-20",
            "multiplier": "100",
        },
    }


class TestIsCloseOut:
    def test_one_close_of_the_whole_holding_is_a_close(self):
        assert bankroll_guard.is_close_out(
            _close(), _snapshot(), working_orders=[]
        )

    def test_a_resting_close_consumes_the_holding(self):
        assert not bankroll_guard.is_close_out(
            _close(), _snapshot(), working_orders=[_resting_close()]
        )

    def test_a_resting_close_leaves_its_remainder_closeable(self):
        assert bankroll_guard.is_close_out(
            _close(quantity=4), _snapshot(), working_orders=[_resting_close(quantity=6)]
        )
        assert not bankroll_guard.is_close_out(
            _close(quantity=5), _snapshot(), working_orders=[_resting_close(quantity=6)]
        )

    def test_partially_filled_resting_close_reserves_only_its_remainder(self):
        # 10 working, 6 already filled: 4 still rest, so 6 stay closeable.
        order = _resting_close(quantity=10, remaining=4)
        order["filled"] = 6.0
        assert bankroll_guard.is_close_out(
            _close(quantity=6), _snapshot(), working_orders=[order]
        )
        assert not bankroll_guard.is_close_out(
            _close(quantity=7), _snapshot(), working_orders=[order]
        )

    def test_a_resting_opening_order_does_not_create_closeable_size(self):
        opening = _resting_close()
        opening["action"] = "BUY"
        assert not bankroll_guard.is_close_out(
            _close(quantity=20), _snapshot(), working_orders=[opening]
        )

    def test_an_unreadable_working_order_book_keeps_the_refusal(self):
        assert not bankroll_guard.is_close_out(
            _close(), _snapshot(), working_orders=None
        )


class TestExemptionSites:
    """Both exemption decisions inherit the accounting from ``is_close_out``."""

    def _strangle_close(self, quantity=10):
        """The canonical close whose SELL envelope prices as new short risk."""
        return {
            "type": "combo",
            "symbol": "SPX",
            "action": "SELL",
            "quantity": quantity,
            "limitPrice": 1.0,
            "legs": [
                {"expiry": "20261218", "strike": 6000.0, "right": "P",
                 "action": "BUY", "ratio": 1},
                {"expiry": "20261218", "strike": 5000.0, "right": "P",
                 "action": "BUY", "ratio": 1},
            ],
        }

    def _held_strangle(self, contracts=10):
        return {
            "last_sync": (NOW - timedelta(minutes=1)).isoformat(),
            "account_summary": {"net_liquidation": 100_000.0},
            "positions": [{
                "ticker": "SPX",
                "legs": [
                    {"type": "Put", "strike": 6000.0, "direction": "LONG",
                     "contracts": contracts, "expiry": "20261218"},
                    {"type": "Put", "strike": 5000.0, "direction": "LONG",
                     "contracts": contracts, "expiry": "20261218"},
                ],
            }],
        }

    def _resting_strangle_close(self, quantity=10):
        return {
            "permId": 900200,
            "action": "SELL",
            "orderType": "LMT",
            "status": "Submitted",
            "totalQuantity": float(quantity),
            "filled": 0.0,
            "remaining": float(quantity),
            "limitPrice": 1.0,
            "contract": {
                "symbol": "SPX",
                "secType": "BAG",
                "comboLegs": [
                    {"conId": 1, "ratio": 1, "action": "BUY", "secType": "OPT",
                     "symbol": "SPX", "strike": 6000.0, "right": "P",
                     "expiry": "2026-12-18"},
                    {"conId": 2, "ratio": 1, "action": "BUY", "secType": "OPT",
                     "symbol": "SPX", "strike": 5000.0, "right": "P",
                     "expiry": "2026-12-18"},
                ],
            },
        }

    @pytest.fixture(autouse=True)
    def _pinned(self, monkeypatch):
        monkeypatch.setattr(order_limits, "max_combo_loss_dollars", lambda: 10_000_000.0)
        monkeypatch.setattr(bankroll_guard, "_utcnow", lambda: NOW)
        monkeypatch.setattr(
            bankroll_guard, "_load_latest_snapshot", lambda: self._held_strangle()
        )

    def test_second_close_is_refused_by_the_placement_cap(self, monkeypatch):
        monkeypatch.setattr(bankroll_guard, "_load_working_orders", lambda: [])
        assert order_limits.check_placement_limits(self._strangle_close()) is None

        monkeypatch.setattr(
            bankroll_guard,
            "_load_working_orders",
            lambda: [self._resting_strangle_close()],
        )
        violation = order_limits.check_placement_limits(self._strangle_close())
        assert violation is not None
        assert violation["code"] == "ORDER_MAX_LOSS_LIMIT"

    def test_the_remainder_of_a_partial_resting_close_is_still_closeable(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            bankroll_guard,
            "_load_working_orders",
            lambda: [self._resting_strangle_close(quantity=6)],
        )
        assert order_limits.check_placement_limits(self._strangle_close(4)) is None
        # Only the ten-lot close reaches the cap; four of ten remain closeable.
        violation = order_limits.check_placement_limits(self._strangle_close(10))
        assert violation is not None
        assert violation["code"] == "ORDER_MAX_LOSS_LIMIT"

    def test_second_close_is_refused_by_the_bankroll_cap(self, monkeypatch):
        # The project conftest admits Gate 3 by default; restore the real check.
        monkeypatch.setattr(
            bankroll_guard, "check_bankroll_admission", _real_bankroll_admission
        )
        stale = {**self._held_strangle(), "account_summary": {"net_liquidation": None}}

        monkeypatch.setattr(bankroll_guard, "_load_working_orders", lambda: [])
        assert _real_bankroll_admission(
            self._strangle_close(), snapshot=stale, now=NOW
        ) is None

        monkeypatch.setattr(
            bankroll_guard,
            "_load_working_orders",
            lambda: [self._resting_strangle_close()],
        )
        refusal = _real_bankroll_admission(
            self._strangle_close(), snapshot=stale, now=NOW
        )
        assert refusal is not None
        assert refusal["code"] == "BANKROLL_UNAVAILABLE"
