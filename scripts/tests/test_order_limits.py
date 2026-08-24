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
def test_risk_reversal_notional_is_the_debit_not_the_put_strike(monkeypatch):
    """Production 2026-08-21: 100-lot 200P/205C risk reversal @ $0.47 debit.

    The ticket showed $4,700 notional (qty × mid × 100). The funnel refused
    with $2,000,000 — assignment-to-zero on the short put
    (100 × $200 × 100). RADON_MAX_ORDER_NOTIONAL is qty × price ×
    multiplier. Assignment risk is a separate loss cap.
    """
    monkeypatch.setenv("RADON_MAX_ORDER_NOTIONAL", "1000000")
    # $2M of assignment sits under the operator-tunable combo loss cap
    # (RADON_MAX_COMBO_LOSS_DOLLARS, default $10M — T-080).
    params = {
        "type": "combo", "symbol": "XYZ", "action": "BUY",
        "quantity": 100, "limitPrice": 0.47,
        "legs": [
            {"expiry": "20260821", "strike": 200, "right": "P", "action": "SELL", "ratio": 1},
            {"expiry": "20260821", "strike": 205, "right": "C", "action": "BUY", "ratio": 1},
        ],
    }
    assert order_limits.order_notional(params) == pytest.approx(4_700.0)
    assert order_limits.check_order_limits(params) is None


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
            "type": "combo", "symbol": "SPY", "action": "BUY",
            "quantity": 500, "limitPrice": -0.20,
            "legs": [_strangle_leg(700, "C"), _strangle_leg(600, "P")],
        })
        assert violation is not None
        assert violation["code"] == "ORDER_MAX_LOSS_LIMIT"

    def test_short_strangle_notional_is_the_credit_and_loss_is_the_strikes(self):
        """Notional is qty × |credit| × 100 = $10k. Assignment is
        (700 + 600) × 100 × 500 minus the $10k collected."""
        params = {
            "type": "combo", "symbol": "SPY", "action": "BUY",
            "quantity": 500, "limitPrice": -0.20,
            "legs": [_strangle_leg(700, "C"), _strangle_leg(600, "P")],
        }
        assert order_limits.order_notional(params) == pytest.approx(10_000.0)
        assert order_limits.combo_max_loss(params) == pytest.approx(64_990_000.0)

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

    def test_defined_risk_width_is_what_trips_the_loss_cap(self):
        """A vertical whose width is a fat-finger (200 lots × 550-wide =
        $11M) is refused on max loss. The debit is only $2/lot ($40k
        notional), under RADON_MAX_ORDER_NOTIONAL."""
        violation = order_limits.check_order_limits({
            "type": "combo", "symbol": "SPX", "action": "BUY",
            "quantity": 200, "limitPrice": 2.0,
            "legs": [
                {"expiry": "20260918", "strike": 7000, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 7550, "right": "C", "action": "SELL", "ratio": 1},
            ],
        })
        assert violation is not None
        assert violation["code"] == "ORDER_MAX_LOSS_LIMIT"

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
            "type": "combo", "symbol": "SPX", "action": "BUY",
            "quantity": 1, "limitPrice": -4.0,
            "legs": [
                {"expiry": "20260918", "strike": 7100, "right": "C", "action": "SELL", "ratio": 1},
                {"expiry": "20260918", "strike": 7110, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 6900, "right": "P", "action": "SELL", "ratio": 1},
                {"expiry": "20260918", "strike": 6880, "right": "P", "action": "BUY", "ratio": 1},
            ],
        }
        # Notional is the $4 credit × 100. Loss is 10-wide + 20-wide minus credit.
        assert order_limits.order_notional(params) == pytest.approx(400.0)
        assert order_limits.combo_max_loss(params) == pytest.approx(2_600.0)

    def test_ratio_spread_leaves_the_uncovered_short_uncovered(self):
        """Buy 1 / sell 2: one short is paired against the long, the other is
        naked and prices at its strike."""
        params = {
            "type": "combo", "symbol": "SPX", "action": "BUY",
            "quantity": 1, "limitPrice": -1.0,
            "legs": [
                {"expiry": "20260918", "strike": 7000, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 7050, "right": "C", "action": "SELL", "ratio": 2},
            ],
        }
        # Notional is the $1 credit × 100. Loss is 50-wide + naked 7050.
        assert order_limits.order_notional(params) == pytest.approx(100.0)
        assert order_limits.combo_max_loss(params) == pytest.approx(709_900.0)

    def test_gld_spread_close_nets_the_credit_against_the_width(self):
        """Production repro 2026-08-19: SELL 100x GLD 420/450 bull call spread
        @ $15 credit (a close of a held position). Gross width is $300k, but
        the order COLLECTS $150k — worst-case exposure is width − credit =
        $150k, under the $250k cap. Measuring gross width refused a
        legitimate close."""
        params = {
            "type": "combo", "symbol": "GLD", "action": "SELL",
            "quantity": 100, "limitPrice": 15.0,
            "legs": [
                {"expiry": "20260828", "strike": 420, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260828", "strike": 450, "right": "C", "action": "SELL", "ratio": 1},
            ],
        }
        assert order_limits.order_notional(params) == pytest.approx(150_000.0)
        assert order_limits.check_order_limits(params) is None

    def test_chain_builder_credit_spread_nets_the_signed_credit(self):
        """Chain-builder convention: BUY envelope, negative limitPrice = net
        credit (commit 1db9f558). Same structure as the SELL-envelope close
        must measure the same."""
        params = {
            "type": "combo", "symbol": "GLD", "action": "BUY",
            "quantity": 100, "limitPrice": -15.0,
            "legs": [
                {"expiry": "20260828", "strike": 420, "right": "C", "action": "SELL", "ratio": 1},
                {"expiry": "20260828", "strike": 450, "right": "C", "action": "BUY", "ratio": 1},
            ],
        }
        assert order_limits.order_notional(params) == pytest.approx(150_000.0)
        assert order_limits.check_order_limits(params) is None

    def test_absurd_credit_cannot_zero_out_the_risk_measure(self):
        """Fat-fingered credit large enough to swallow the strike risk must
        not slip the cap: the premium term counts the same dollars, so
        max(premium, risk − credit) never drops below half the strike risk."""
        violation = order_limits.check_order_limits({
            "type": "combo", "symbol": "SPY", "action": "BUY",
            "quantity": 500, "limitPrice": -1500.0,
            "legs": [_strangle_leg(700, "C"), _strangle_leg(600, "P")],
        })
        assert violation is not None
        assert violation["code"] == "ORDER_NOTIONAL_LIMIT"

    def test_debit_combos_do_not_net(self):
        """A debit pays premium on top of carrying the structure — netting
        applies only when the order collects premium."""
        notional = order_limits.order_notional({
            "type": "combo", "symbol": "SPX", "action": "BUY",
            "quantity": 100, "limitPrice": 2.0,
            "legs": [
                {"expiry": "20260918", "strike": 7000, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 7050, "right": "C", "action": "SELL", "ratio": 1},
            ],
        })
        assert notional == pytest.approx(20_000.0)
        assert order_limits.combo_max_loss({
            "type": "combo", "symbol": "SPX", "action": "BUY",
            "quantity": 100, "limitPrice": 2.0,
            "legs": [
                {"expiry": "20260918", "strike": 7000, "right": "C", "action": "BUY", "ratio": 1},
                {"expiry": "20260918", "strike": 7050, "right": "C", "action": "SELL", "ratio": 1},
            ],
        }) == pytest.approx(500_000.0)

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


class TestComboLossCapIsOperatorTunable:
    """T-080 — the combo worst-case-loss cap was a hardcoded $10M with
    nothing between it and the $250k notional cap. It keeps $10M as the
    default but resolves through ``app_preferences`` like the other limits."""

    def _seventy_lot_strangle(self):
        # BUY envelope + negative price = credit received (chain-builder
        # encoding, R-086 four-quadrant rule), so the $0.20 nets off risk.
        return {
            "type": "combo", "symbol": "SPY", "action": "BUY",
            "quantity": 70, "limitPrice": -0.20,
            "legs": [_strangle_leg(700, "C"), _strangle_leg(600, "P")],
        }

    def test_default_is_ten_million(self):
        assert order_limits.max_combo_loss_dollars() == pytest.approx(10_000_000.0)

    def test_seventy_lot_strangle_clears_the_default(self):
        loss = order_limits.combo_max_loss(self._seventy_lot_strangle())
        assert loss == pytest.approx(9_098_600.0)
        assert order_limits.check_order_limits(self._seventy_lot_strangle()) is None

    def test_lowered_cap_refuses_the_same_order(self, monkeypatch):
        monkeypatch.setenv("RADON_MAX_COMBO_LOSS_DOLLARS", "1000000")
        violation = order_limits.check_order_limits(self._seventy_lot_strangle())
        assert violation is not None
        assert violation["code"] == "ORDER_MAX_LOSS_LIMIT"
        assert "RADON_MAX_COMBO_LOSS_DOLLARS" in violation["message"]

    def test_boundary_at_the_cap(self, monkeypatch):
        """Loss exactly at the cap places; one dollar over is refused."""
        loss = order_limits.combo_max_loss(self._seventy_lot_strangle())
        monkeypatch.setenv("RADON_MAX_COMBO_LOSS_DOLLARS", str(loss))
        assert order_limits.check_order_limits(self._seventy_lot_strangle()) is None
        monkeypatch.setenv("RADON_MAX_COMBO_LOSS_DOLLARS", str(loss - 1))
        violation = order_limits.check_order_limits(self._seventy_lot_strangle())
        assert violation is not None and violation["code"] == "ORDER_MAX_LOSS_LIMIT"

    def test_env_above_hard_ceiling_is_clamped(self, monkeypatch):
        """The band tops out at $50M; an env value cannot lift the cap past it."""
        monkeypatch.setenv("RADON_MAX_COMBO_LOSS_DOLLARS", "999999999999")
        assert order_limits.max_combo_loss_dollars() == pytest.approx(50_000_000.0)
