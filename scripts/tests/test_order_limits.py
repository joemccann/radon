#!/usr/bin/env python3
"""REL-005 — server-side order limits (RELIABILITY_AUDIT.md R-002, R-006).

Before this, the only bounds between any caller and IB were
``quantity > 0`` and ``limitPrice > 0`` — no upper bound anywhere, and
the workflow bridge could place one live order per scanner row with no
cap. These tests pin the authoritative server-side limits at the
``ib_place_order.place_order`` funnel and the workflow emit cap.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import order_limits


class TestCheckOrderLimits:
    def test_normal_option_order_allowed(self):
        violation = order_limits.check_order_limits({
            "type": "option", "symbol": "GOOG", "action": "BUY",
            "quantity": 44, "limitPrice": 12.0,
        })
        assert violation is None

    def test_oversized_quantity_refused(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "100")
        violation = order_limits.check_order_limits({
            "type": "option", "symbol": "GOOG", "action": "BUY",
            "quantity": 101, "limitPrice": 1.0,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_QTY_LIMIT"

    def test_oversized_notional_refused(self, monkeypatch):
        """250 contracts × $40 × 100 = $1M > default $250k cap."""
        violation = order_limits.check_order_limits({
            "type": "option", "symbol": "CRCL", "action": "BUY",
            "quantity": 250, "limitPrice": 40.0,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_stp_notional_uses_stop_price(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_NOTIONAL", "1000")
        violation = order_limits.check_order_limits({
            "type": "stock", "symbol": "AAPL", "action": "SELL",
            "quantity": 100, "orderType": "STP", "stopPrice": 20.0,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_stock_notional_uses_multiplier_one(self):
        """1000 shares × $200 = $200k — under the default cap; the option
        multiplier must not apply to stock."""
        violation = order_limits.check_order_limits({
            "type": "stock", "symbol": "AAPL", "action": "BUY",
            "quantity": 1000, "limitPrice": 200.0,
        })
        assert violation is None

    def test_env_overrides_respected(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_NOTIONAL", "1000")
        violation = order_limits.check_order_limits({
            "type": "stock", "symbol": "AAPL", "action": "BUY",
            "quantity": 100, "limitPrice": 20.0,
        })
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    @pytest.mark.parametrize("ratio", [0, -1, 1.5, 101])
    def test_combo_ratio_must_be_bounded_positive_integer(self, ratio):
        violation = order_limits.check_order_limits({
            "type": "combo", "quantity": 1, "limitPrice": 1,
            "legs": [{"ratio": ratio}, {"ratio": 1}],
        })
        assert violation is not None
        assert violation["code"] == "ORDER_COMBO_RATIO"

    def test_combo_effective_contracts_are_capped(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "100")
        violation = order_limits.check_order_limits({
            "type": "combo", "quantity": 51, "limitPrice": 1,
            "legs": [{"ratio": 2}, {"ratio": 1}],
        })
        assert violation is not None
        assert violation["code"] == "ORDER_EFFECTIVE_QTY_LIMIT"


class TestPlacementFunnelEnforcement:
    def test_place_order_refuses_oversized_before_ib(self, monkeypatch, tmp_path):
        import ib_place_order
        import trading_halt

        monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "100")
        with patch.object(ib_place_order, "IBClient", side_effect=AssertionError(
            "IBClient must not be constructed for an over-limit order"
        )):
            result = ib_place_order.place_order({
                "type": "option", "symbol": "GOOG", "action": "BUY",
                "quantity": 5000, "limitPrice": 40.0,
            })
        assert result["status"] == "error"
        assert "limit" in result["message"].lower()

    def test_what_if_preview_exempt(self, monkeypatch, tmp_path):
        """what-if is read-only (no transmit) — the risk preview must be
        able to price an over-limit order so the UI can explain WHY."""
        import ib_place_order
        import trading_halt

        monkeypatch.setattr(trading_halt, "HALT_FILE", tmp_path / "halt.json")
        monkeypatch.setenv("RADON_MAX_ORDER_QTY", "100")
        constructed = {}

        class _FailsAtConnect:
            def __init__(self):
                constructed["yes"] = True

            def connect(self, *a, **k):
                raise ConnectionRefusedError("no gateway in tests")

        with patch.object(ib_place_order, "IBClient", _FailsAtConnect):
            result = ib_place_order.place_order(
                {"type": "option", "symbol": "GOOG", "action": "BUY",
                 "quantity": 5000, "limitPrice": 40.0},
                what_if=True,
            )
        # Reached the IB layer (constructed) instead of the limit refusal.
        assert constructed.get("yes") is True
        assert "limit" not in (result.get("message") or "").lower()


class TestWorkflowEmitCap:
    def test_emit_refuses_over_cap_atomically(self, monkeypatch):
        from workflow import nodes

        monkeypatch.setenv("RADON_WORKFLOW_MAX_ORDERS", "3")
        placed = []
        monkeypatch.setattr(nodes, "run_order_placement", lambda p: placed.append(p))

        rows = [
            {"ticker": f"T{i}", "action": "BUY", "quantity": 1, "limit_price": 1.0}
            for i in range(5)
        ]
        with pytest.raises(Exception) as exc_info:
            nodes.emit_order(rows, {})
        assert "cap" in str(exc_info.value).lower()
        assert placed == []  # atomic refusal — no partial mass-placement

    def test_emit_within_cap_places_each(self, monkeypatch):
        from workflow import nodes

        monkeypatch.setenv("RADON_WORKFLOW_MAX_ORDERS", "3")
        placed = []
        monkeypatch.setattr(nodes, "run_order_placement", lambda p: placed.append(p))

        rows = [
            {"ticker": f"T{i}", "action": "BUY", "quantity": 1, "limit_price": 1.0}
            for i in range(2)
        ]
        nodes.emit_order(rows, {})
        assert len(placed) == 2


# ---------------------------------------------------------------------------
# REL-028 (R-052) — commit 1db9f558 made a combo limitPrice a SIGNED net
# price, but order_notional priced the cap off |limitPrice|, i.e. off the net
# CREDIT rather than the risk. A 500-lot short strangle at $0.20 credit
# measured as $10,000 of "notional" and cleared the $250k cap with room to
# spare, leaving the contract-count cap as the only server-side bound on an
# undefined-risk position. With Gate 4 disabled that puts IB's margin engine
# back as the backstop — which is what REL-005 existed to stop being true.
# ---------------------------------------------------------------------------


def _strangle_leg(strike: float, right: str) -> dict:
    return {
        "expiry": "20260918", "strike": strike, "right": right,
        "action": "SELL", "ratio": 1,
    }


class TestComboNotionalIsRiskNotPremium:
    def test_short_strangle_at_a_small_credit_is_refused(self):
        """The R-052 case: 500 lots, $0.20 net credit, undefined risk."""
        violation = order_limits.check_order_limits({
            "type": "combo", "symbol": "SPY", "action": "SELL",
            "quantity": 500, "limitPrice": -0.20,
            "legs": [_strangle_leg(700, "C"), _strangle_leg(600, "P")],
        })
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_short_strangle_notional_prices_the_risk(self):
        """(700 + 600) × 100 × 500 = $65M of exposure, not $10k of credit."""
        notional = order_limits.order_notional({
            "type": "combo", "symbol": "SPY", "action": "SELL",
            "quantity": 500, "limitPrice": -0.20,
            "legs": [_strangle_leg(700, "C"), _strangle_leg(600, "P")],
        })
        assert notional == pytest.approx(65_000_000.0)

    def test_defined_risk_debit_spread_within_budget_still_places(self):
        """Control: 10 lots of a 5-wide vertical is $5,000 of risk."""
        violation = order_limits.check_order_limits({
            "type": "combo", "symbol": "SPX", "action": "BUY",
            "quantity": 10, "limitPrice": 2.0,
            "legs": [
                {"expiry": "20260918", "strike": 7000, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 7005, "right": "C", "action": "SELL", "ratio": 1},
            ],
        })
        assert violation is None

    def test_defined_risk_width_is_what_trips_the_cap(self):
        """A vertical wide enough to genuinely risk >$250k is refused, and it
        is the WIDTH that trips it — the debit is only $2/lot."""
        violation = order_limits.check_order_limits({
            "type": "combo", "symbol": "SPX", "action": "BUY",
            "quantity": 100, "limitPrice": 2.0,
            "legs": [
                {"expiry": "20260918", "strike": 7000, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 7050, "right": "C", "action": "SELL", "ratio": 1},
            ],
        })
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_calendar_spread_is_priced_off_the_debit_not_the_strike(self):
        """A long calendar's max loss is the debit paid: the long leg covers
        the short at the same strike, so the width is zero. Pricing it off the
        strike would refuse every index calendar the chain builder emits."""
        params = {
            "type": "combo", "symbol": "NVDA", "action": "BUY",
            "quantity": 5, "limitPrice": 3.0,
            "legs": [
                {"expiry": "20261016", "strike": 180, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 180, "right": "C", "action": "SELL", "ratio": 1},
            ],
        }
        assert order_limits.order_notional(params) == pytest.approx(1_500.0)
        assert order_limits.check_order_limits(params) is None

    def test_iron_condor_sums_both_wings(self):
        params = {
            "type": "combo", "symbol": "SPX", "action": "SELL",
            "quantity": 1, "limitPrice": -4.0,
            "legs": [
                {"expiry": "20260918", "strike": 7100, "right": "C", "action": "SELL", "ratio": 1},
                {"expiry": "20260918", "strike": 7110, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 6900, "right": "P", "action": "SELL", "ratio": 1},
                {"expiry": "20260918", "strike": 6880, "right": "P", "action": "BUY", "ratio": 1},
            ],
        }
        # 10-wide call wing + 20-wide put wing = $3,000 per unit.
        assert order_limits.order_notional(params) == pytest.approx(3_000.0)

    def test_ratio_spread_leaves_the_uncovered_short_uncovered(self):
        """Buy 1 / sell 2: one short is paired against the long, the other is
        naked and prices at its strike."""
        params = {
            "type": "combo", "symbol": "SPX", "action": "SELL",
            "quantity": 1, "limitPrice": -1.0,
            "legs": [
                {"expiry": "20260918", "strike": 7000, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 7050, "right": "C", "action": "SELL", "ratio": 2},
            ],
        }
        # 50-wide covered pair + one naked 7050 short = (50 + 7050) × 100.
        assert order_limits.order_notional(params) == pytest.approx(710_000.0)

    def test_unpriceable_legs_fall_back_to_premium(self):
        """Missing strikes must not silently produce a zero risk number."""
        params = {
            "type": "combo", "symbol": "SPX", "action": "BUY",
            "quantity": 2, "limitPrice": 3.0,
            "legs": [
                {"expiry": "20260918", "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "right": "C", "action": "SELL", "ratio": 1},
            ],
        }
        assert order_limits.order_notional(params) == pytest.approx(600.0)
