"""REL-041 / R-086 + R-087 (P1) — the combo risk gate has three holes.

R-086: `_combo_credit_per_unit` books a credit whenever `signed_price < 0`
OR the envelope is SELL. IB's BAG sign convention has FOUR quadrants, and
the fourth — a SELL envelope at a NEGATIVE net price, i.e. closing or
rolling a short structure for a DEBIT — is booked as collecting
`|price| x 100` per unit. That phantom credit is subtracted from
`risk_per_unit`, so a 500-lot SELL combo on a $5-wide short vertical at
`limitPrice = -5.00` prices at `combo_max_loss == $0` on an order paying
$250,000.

R-087: 38ccbcbf moved combo risk to a hardcoded `_MAX_COMBO_LOSS_DOLLARS =
10_000_000` — a 40x widening of the $250k notional cap that used to bound
combos, and not operator-tunable without a deploy. And `combo_max_loss`
returns `None` — i.e. NO loss check at all — whenever any leg lacks a
positive `strike`, which `check_order_limits` never validates.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import order_limits


def _vertical(action: str, price: float, quantity: int = 1) -> dict:
    """$5-wide short call vertical: short 100, long 105."""
    return {
        "type": "combo",
        "action": action,
        "quantity": quantity,
        "limitPrice": price,
        "legs": [
            {"action": "SELL", "right": "C", "strike": 100.0, "ratio": 1},
            {"action": "BUY", "right": "C", "strike": 105.0, "ratio": 1},
        ],
    }


class TestFourQuadrants:
    @pytest.mark.parametrize(
        "action,price,expected,why",
        [
            # BUY envelope, positive price: paying a debit, no credit.
            ("BUY", 5.00, 500.0, "debit paid, full width at risk"),
            # BUY envelope, negative price: the chain builder's credit
            # encoding (commit 1db9f558).
            ("BUY", -5.00, 0.0, "credit collected covers the width"),
            # SELL envelope, positive price: the close ticket's credit
            # encoding.
            ("SELL", 5.00, 0.0, "credit collected covers the width"),
            # SELL envelope, NEGATIVE price: closing/rolling a short
            # structure for a DEBIT. R-086's missing quadrant.
            ("SELL", -5.00, 500.0, "debit paid, full width at risk"),
        ],
    )
    def test_combo_max_loss_over_every_sign_quadrant(self, action, price, expected, why):
        assert order_limits.combo_max_loss(_vertical(action, price)) == expected, why

    def test_sell_envelope_at_a_negative_price_is_priced_and_then_capped(self):
        """500 lots x $5.00 debit = $250,000 paid. It used to price at $0
        loss; now it prices at $250,000 — exactly the cap, so it still
        transmits — and one more lot is refused quoting the real number."""
        at_cap = _vertical("SELL", -5.00, quantity=500)
        assert order_limits.combo_max_loss(at_cap) == 250_000.0
        assert order_limits.check_order_limits(at_cap) is None

        # 501 lots would trip the contract cap first, so widen the price by
        # a dollar instead: 500 lots x $6.00 = $300,000 paid.
        over = _vertical("SELL", -6.00, quantity=500)
        verdict = order_limits.check_order_limits(over)
        assert verdict is not None, "a $300,000 debit priced as zero risk transmitted"
        assert verdict["code"] in {"ORDER_MAX_LOSS_LIMIT", "ORDER_NOTIONAL_LIMIT"}, verdict
        assert "300,000" in verdict["message"], verdict


class TestComboLossBand:
    def test_default_band_refuses_a_500_lot_strangle_on_a_200_dollar_name(self):
        """$19.99M of assignment risk on a 500-lot naked strangle is refused
        by the $10M default (the operator's T-080 call); nothing below the
        cap is silently unbounded."""
        strangle = {
            "type": "combo",
            "action": "BUY",
            "quantity": 500,
            "limitPrice": -0.20,
            "legs": [
                {"action": "SELL", "right": "C", "strike": 200.0, "ratio": 1},
                {"action": "SELL", "right": "P", "strike": 200.0, "ratio": 1},
            ],
        }
        verdict = order_limits.check_order_limits(strangle)
        assert verdict is not None, "a 500-lot naked strangle transmitted"
        assert verdict["code"] == "ORDER_MAX_LOSS_LIMIT", verdict

    def test_band_is_operator_tunable(self, monkeypatch):
        """Raising the preference changes the verdict — no deploy needed."""
        strangle = {
            "type": "combo",
            "action": "BUY",
            "quantity": 500,
            "limitPrice": -0.20,
            "legs": [
                {"action": "SELL", "right": "C", "strike": 50.0, "ratio": 1},
                {"action": "SELL", "right": "P", "strike": 50.0, "ratio": 1},
            ],
        }
        monkeypatch.setenv("RADON_MAX_COMBO_LOSS_DOLLARS", "1000000")
        assert order_limits.check_order_limits(strangle) is not None

        monkeypatch.setenv("RADON_MAX_COMBO_LOSS_DOLLARS", "6000000")
        assert order_limits.check_order_limits(strangle) is None

    def test_the_default_is_the_operator_decided_ten_million(self):
        """R-087 asked for the notional cap as the default. The operator's
        call (T-080, 2026-08-23) was to keep $10M as the default and make it
        a visible, tunable preference (RADON_MAX_COMBO_LOSS_DOLLARS) instead
        of a hardcoded constant."""
        assert order_limits.max_combo_loss_dollars() == 10_000_000.0
        assert order_limits.max_combo_loss_dollars() > order_limits.max_order_notional()


class TestUnpriceableComboIsRefused:
    def test_missing_strike_is_refused_not_silently_unbounded(self):
        combo = _vertical("BUY", 5.00, quantity=100)
        del combo["legs"][0]["strike"]

        verdict = order_limits.check_order_limits(combo)

        assert verdict is not None, (
            "a combo whose loss cannot be priced skipped the loss check entirely"
        )
        assert verdict["code"] == "ORDER_COMBO_STRIKE", verdict

    @pytest.mark.parametrize("bad", [0, -5.0, "abc", None])
    def test_non_positive_or_unparseable_strikes_are_refused(self, bad):
        combo = _vertical("BUY", 5.00, quantity=100)
        combo["legs"][1]["strike"] = bad
        assert order_limits.check_order_limits(combo) is not None

    def test_a_stock_leg_combo_is_not_forced_to_carry_a_strike(self):
        """Control: only OPTION legs need a strike."""
        combo = {
            "type": "combo",
            "action": "BUY",
            "quantity": 1,
            "limitPrice": 5.00,
            "legs": [
                {"action": "SELL", "right": "C", "strike": 100.0, "ratio": 1},
                {"action": "BUY", "sec_type": "STK", "ratio": 100},
            ],
        }
        verdict = order_limits.check_order_limits(combo)
        assert verdict is None or verdict["code"] != "ORDER_COMBO_STRIKE", verdict
