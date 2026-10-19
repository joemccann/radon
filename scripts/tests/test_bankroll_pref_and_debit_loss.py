"""The Off preference bypasses Gate 3 on place_order too, and a debit
structure's max loss is the premium paid, not the spread width."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import app_preferences
import bankroll_guard

PREF = "RADON_BANKROLL_CAP_ENFORCE_ALL_PATHS"
REAL_CHECK = bankroll_guard.check_bankroll_admission


def _snapshot(nlv=1_278_369.0):
    return {
        "last_sync": datetime.now(timezone.utc).isoformat(),
        "account_summary": {"net_liquidation": nlv},
        "positions": [],
    }


def _combo(legs, *, quantity, price, action="BUY"):
    return {"type": "combo", "symbol": "SPY", "action": action,
            "quantity": quantity, "limitPrice": price,
            "legs": [{"expiry": "20261120", "ratio": 1, **leg} for leg in legs]}


BULL_CALL = [{"strike": 600, "right": "C", "action": "BUY"},
             {"strike": 610, "right": "C", "action": "SELL"}]


@pytest.fixture
def real_guard(monkeypatch):
    monkeypatch.setattr(bankroll_guard, "check_bankroll_admission", REAL_CHECK)
    monkeypatch.setattr(bankroll_guard, "_load_latest_snapshot", lambda: _snapshot())
    monkeypatch.delenv(PREF, raising=False)
    app_preferences.clear_snapshot_for_tests()
    yield
    app_preferences.clear_snapshot_for_tests()


def _place(params, monkeypatch):
    import ib_place_order
    client = MagicMock()
    client.return_value.connect.side_effect = RuntimeError("stop at connect")
    monkeypatch.setattr(ib_place_order, "IBClient", client)
    return ib_place_order.place_order(params), client


class TestPreferenceOffBypassesPlaceOrder:
    def test_off_reaches_ib_even_over_cap(self, real_guard, monkeypatch):
        big = _combo(BULL_CALL, quantity=200, price=6.0)
        result, client = _place(big, monkeypatch)
        assert result.get("code") != "BANKROLL_CAP_EXCEEDED", result
        client.return_value.connect.assert_called_once()

    def test_on_refuses_over_cap(self, real_guard, monkeypatch):
        app_preferences.seed_snapshot_for_tests({PREF: "true"})
        big = _combo(BULL_CALL, quantity=200, price=6.0)
        result, client = _place(big, monkeypatch)
        assert result.get("code") == "BANKROLL_CAP_EXCEEDED", result
        client.assert_not_called()


class TestDebitMaxLossIsPremium:
    def test_bull_call_debit_is_premium_not_width(self):
        # 100 x $10-wide at $2.00 debit: pays $20,000; width would be $100,000.
        assert bankroll_guard.order_max_loss(_combo(BULL_CALL, quantity=100, price=2.0)) == pytest.approx(20_000)

    def test_bear_put_debit_is_premium(self):
        legs = [{"strike": 610, "right": "P", "action": "BUY"},
                {"strike": 600, "right": "P", "action": "SELL"}]
        assert bankroll_guard.order_max_loss(_combo(legs, quantity=10, price=3.0)) == pytest.approx(3_000)

    def test_credit_vertical_is_width_minus_credit(self):
        legs = [{"strike": 600, "right": "P", "action": "SELL"},
                {"strike": 590, "right": "P", "action": "BUY"}]
        assert bankroll_guard.order_max_loss(_combo(legs, quantity=10, price=-2.0)) == pytest.approx(8_000)

    def test_long_call_butterfly_is_premium(self):
        legs = [{"strike": 590, "right": "C", "action": "BUY"},
                {"strike": 600, "right": "C", "action": "SELL", "ratio": 2},
                {"strike": 610, "right": "C", "action": "BUY"}]
        assert bankroll_guard.order_max_loss(_combo(legs, quantity=10, price=1.5)) == pytest.approx(1_500)

    def test_risk_reversal_short_put_still_priced_to_zero(self):
        legs = [{"strike": 200, "right": "P", "action": "SELL"},
                {"strike": 205, "right": "C", "action": "BUY"}]
        # Stock to zero: 200*100 put loss, plus 0.5 debit, per unit.
        assert bankroll_guard.order_max_loss(_combo(legs, quantity=1, price=0.5)) == pytest.approx(20_050)

    def test_debit_vertical_under_cap_is_admitted(self):
        # 100 x $10-wide for $2.00 = $20k on $1.28M NLV (1.6%): width math refused this.
        verdict = REAL_CHECK(_combo(BULL_CALL, quantity=100, price=2.0), snapshot=_snapshot())
        assert verdict is None


class TestStopOrderPricing:
    """Gate 3 must price a stop order on the price IB actually sends.

    ``ib_place_order`` builds ``StopOrder(stopPrice=...)`` for a STP order and
    drops ``limitPrice`` entirely, so a decoy limit understated max loss.
    """

    def _stock(self, **extra):
        return {"type": "stock", "symbol": "AAPL", "action": "BUY",
                "quantity": 10_000, **extra}

    def test_stp_max_loss_uses_the_stop_price(self):
        loss = bankroll_guard.order_max_loss(
            self._stock(orderType="STP", limitPrice=0.01, stopPrice=1000.0))
        assert loss == pytest.approx(10_000_000)

    def test_stp_limit_max_loss_uses_the_worse_price(self):
        loss = bankroll_guard.order_max_loss(
            self._stock(orderType="STP LMT", limitPrice=0.01, stopPrice=1000.0))
        assert loss == pytest.approx(10_000_000)

    def test_stp_max_loss_never_prices_below_a_supplied_limit(self):
        loss = bankroll_guard.order_max_loss(
            self._stock(orderType="STP", limitPrice=1000.0, stopPrice=0.01))
        assert loss == pytest.approx(10_000_000)

    def test_limit_order_still_prices_on_its_limit(self):
        loss = bankroll_guard.order_max_loss(
            self._stock(orderType="LMT", limitPrice=20.0))
        assert loss == pytest.approx(200_000)

    def test_decoy_limit_no_longer_admits_an_over_cap_stop(self):
        verdict = REAL_CHECK(
            self._stock(orderType="STP", limitPrice=0.01, stopPrice=1000.0),
            snapshot=_snapshot())
        assert verdict is not None
        assert verdict.get("code") == "BANKROLL_CAP_EXCEEDED", verdict


class TestKellyGuardSharesTheSamePricing:
    """kelly_guard is the third guard reading an order price; a decoy field
    must not give it a different answer from Gate 3."""

    def test_option_stop_worst_case_loss_matches_gate_3(self):
        import kelly_guard

        params = {"type": "option", "symbol": "AAPL", "action": "BUY",
                  "quantity": 400, "orderType": "STP",
                  "limitPrice": 0.01, "stopPrice": 50.0}
        assert kelly_guard._worst_case_loss(params) == pytest.approx(2_000_000)
        assert bankroll_guard.order_max_loss(params) == pytest.approx(2_000_000)
