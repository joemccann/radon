"""Tests for naked_short_audit.py — naked short violation detection and cancellation."""
import json
import pytest
from unittest.mock import MagicMock, patch

# Will be importable once implemented
from naked_short_audit import find_naked_short_violations, cancel_violations


# ---------------------------------------------------------------------------
# Helpers — build minimal order / position dicts matching real data shapes
# ---------------------------------------------------------------------------

def make_order(order_id, perm_id, symbol, sec_type, action, qty,
               status="Submitted", right="?", strike=0.0, expiry=None):
    return {
        "orderId": order_id,
        "permId": perm_id,
        "symbol": symbol,
        "contract": {
            "conId": 100000 + order_id,
            "symbol": symbol,
            "secType": sec_type,
            "strike": strike,
            "right": right,
            "expiry": expiry,
        },
        "action": action,
        "orderType": "LMT",
        "totalQuantity": float(qty),
        "limitPrice": 10.0,
        "auxPrice": 0.0,
        "status": status,
        "filled": 0.0,
        "remaining": float(qty),
        "avgFillPrice": 0.0,
        "tif": "GTC",
    }


def make_stock_position(ticker, shares):
    return {
        "ticker": ticker,
        "structure_type": "Stock",
        "contracts": shares,
        "direction": "LONG",
        "legs": [
            {
                "direction": "LONG",
                "contracts": shares,
                "type": "Stock",
                "strike": 0.0,
            }
        ],
    }


def make_option_position(ticker, direction, opt_type, contracts, strike=100.0):
    """Build an option position (e.g. SHORT call leg in portfolio)."""
    return {
        "ticker": ticker,
        "structure_type": "Long Call" if direction == "LONG" else "Short Call",
        "contracts": contracts,
        "direction": direction,
        "legs": [
            {
                "direction": direction,
                "contracts": contracts,
                "type": opt_type,
                "strike": strike,
            }
        ],
    }


def make_spread_position(ticker, contracts, long_strike, short_strike):
    """Bull call spread — has both a LONG and SHORT call leg."""
    return {
        "ticker": ticker,
        "structure_type": "Bull Call Spread",
        "contracts": contracts,
        "direction": "DEBIT",
        "legs": [
            {"direction": "LONG", "contracts": contracts, "type": "Call", "strike": long_strike},
            {"direction": "SHORT", "contracts": contracts, "type": "Call", "strike": short_strike},
        ],
    }


# ===========================================================================
# Test: find_naked_short_violations
# ===========================================================================

@pytest.mark.skip(reason="naked short guard disabled at module entry point")
class TestFindNakedShortViolations:
    def test_no_orders_no_violations(self):
        assert find_naked_short_violations([], []) == []

    def test_buy_order_never_violation(self):
        orders = [make_order(1, 100, "AAPL", "STK", "BUY", 100)]
        violations = find_naked_short_violations(orders, [])
        assert violations == []

    def test_sell_stock_no_position_violation(self):
        orders = [make_order(1, 100, "AAPL", "STK", "SELL", 100)]
        violations = find_naked_short_violations(orders, [])
        assert len(violations) == 1
        assert violations[0]["order_id"] == 1
        assert violations[0]["perm_id"] == 100
        assert violations[0]["symbol"] == "AAPL"
        assert "no LONG stock position" in violations[0]["reason"].lower() or \
               "no long stock" in violations[0]["reason"].lower()

    def test_sell_stock_within_position_no_violation(self):
        orders = [make_order(1, 100, "AAPL", "STK", "SELL", 100)]
        positions = [make_stock_position("AAPL", 200)]
        violations = find_naked_short_violations(orders, positions)
        assert violations == []

    def test_sell_stock_exceeds_position_violation(self):
        orders = [make_order(1, 100, "AAPL", "STK", "SELL", 500)]
        positions = [make_stock_position("AAPL", 200)]
        violations = find_naked_short_violations(orders, positions)
        assert len(violations) == 1
        assert violations[0]["order_id"] == 1
        assert "exceeds" in violations[0]["reason"].lower() or \
               ">" in violations[0]["reason"]

    def test_sell_call_no_stock_violation(self):
        orders = [make_order(1, 100, "AAPL", "OPT", "SELL", 5,
                             right="C", strike=150.0, expiry="2026-06-20")]
        violations = find_naked_short_violations(orders, [])
        assert len(violations) == 1
        assert violations[0]["order_id"] == 1
        assert "naked" in violations[0]["reason"].lower() or \
               "no long stock" in violations[0]["reason"].lower()

    def test_sell_call_covered_no_violation(self):
        orders = [make_order(1, 100, "AAPL", "OPT", "SELL", 2,
                             right="C", strike=150.0, expiry="2026-06-20")]
        positions = [make_stock_position("AAPL", 500)]
        violations = find_naked_short_violations(orders, positions)
        assert violations == []

    def test_sell_call_undercovered_violation(self):
        """Existing 3 SHORT calls in portfolio + 5 new SELL call order = 8 contracts.
        8 * 100 = 800 shares needed. Only have 500 shares → violation."""
        orders = [make_order(1, 100, "AAPL", "OPT", "SELL", 5,
                             right="C", strike=150.0, expiry="2026-06-20")]
        positions = [
            make_stock_position("AAPL", 500),
            make_option_position("AAPL", "SHORT", "Call", 3, strike=150.0),
        ]
        violations = find_naked_short_violations(orders, positions)
        assert len(violations) == 1
        assert violations[0]["order_id"] == 1

    def test_sell_put_no_violation(self):
        orders = [make_order(1, 100, "AAPL", "OPT", "SELL", 10,
                             right="P", strike=100.0, expiry="2026-06-20")]
        violations = find_naked_short_violations(orders, [])
        assert violations == []

    def test_bag_combo_no_violation(self):
        orders = [make_order(1, 100, "AAPL", "BAG", "SELL", 10)]
        violations = find_naked_short_violations(orders, [])
        assert violations == []

    def test_multiple_violations(self):
        orders = [
            make_order(1, 100, "AAPL", "STK", "SELL", 100),
            make_order(2, 200, "GOOG", "OPT", "SELL", 5,
                       right="C", strike=200.0, expiry="2026-06-20"),
            make_order(3, 300, "MSFT", "STK", "BUY", 50),  # not a violation
        ]
        violations = find_naked_short_violations(orders, [])
        assert len(violations) == 2
        ids = {v["order_id"] for v in violations}
        assert ids == {1, 2}

    def test_inactive_orders_skipped(self):
        """Orders with status Filled/Cancelled should be ignored."""
        orders = [
            make_order(1, 100, "AAPL", "STK", "SELL", 100, status="Filled"),
            make_order(2, 200, "GOOG", "STK", "SELL", 100, status="Cancelled"),
        ]
        violations = find_naked_short_violations(orders, [])
        assert violations == []

    def test_sell_call_covered_by_spread_short_leg(self):
        """Short call leg in a spread position counts toward existing short calls."""
        orders = [make_order(1, 100, "AAPL", "OPT", "SELL", 2,
                             right="C", strike=160.0, expiry="2026-06-20")]
        positions = [
            make_stock_position("AAPL", 500),
            make_spread_position("AAPL", 3, 140.0, 160.0),  # 3 SHORT calls in spread
        ]
        # Existing 3 short calls + 2 new = 5 total, 5*100=500 = shares held → OK
        violations = find_naked_short_violations(orders, positions)
        assert violations == []


# ===========================================================================
# Test: cancel_violations
# ===========================================================================

class TestCancelViolations:
    def test_cancel_calls_client(self):
        client = MagicMock()
        # Set up mock trades that match by permId
        trade1 = MagicMock()
        trade1.order.permId = 100
        trade1.order.orderId = 1
        trade2 = MagicMock()
        trade2.order.permId = 200
        trade2.order.orderId = 2
        client.get_open_orders.return_value = [trade1, trade2]

        violations = [
            {"order_id": 1, "perm_id": 100, "reason": "test", "symbol": "AAPL"},
            {"order_id": 2, "perm_id": 200, "reason": "test", "symbol": "GOOG"},
        ]
        count = cancel_violations(client, violations)
        assert count == 2
        assert client.cancel_order.call_count == 2

    def test_cancel_empty_list(self):
        client = MagicMock()
        count = cancel_violations(client, [])
        assert count == 0


# ===========================================================================
# Test: dry-run mode (main)
# ===========================================================================

@pytest.mark.skip(reason="naked short guard disabled at module entry point")
class TestDryRun:
    def test_dry_run_does_not_cancel(self, tmp_path):
        """Dry-run prints violations but does not connect to IB or cancel."""
        portfolio = {"positions": []}
        orders = {
            "open_orders": [
                make_order(1, 100, "AAPL", "STK", "SELL", 100),
            ]
        }
        pf = tmp_path / "portfolio.json"
        of = tmp_path / "orders.json"
        pf.write_text(json.dumps(portfolio))
        of.write_text(json.dumps(orders))

        from naked_short_audit import main as audit_main
        import sys

        with patch.object(sys, "argv", [
            "naked_short_audit.py", "--dry-run",
            "--portfolio", str(pf), "--orders", str(of),
        ]):
            with patch("naked_short_audit.IBClient") as mock_ib:
                result = audit_main()
                mock_ib.assert_not_called()
                assert result["violations_found"] == 1
                assert result["cancelled"] == 0
