#!/usr/bin/env python3
"""Regression — expired local option pages as quantity_mismatch forever.

Production incident 2026-08-09: service_health row "position-reconcile"
latched state='error' with last_error:

    {"quantity_mismatch_count": 1,
     "quantity_mismatch": [{"symbol": "SPCX", "sec_type": "OPT",
                            "ib_quantity": 0, "local_quantity": 25,
                            "strike": 150.0, "right": "C",
                            "expiry": "20260807"}]}

The option EXPIRED Friday; detection was Sunday. After expiry IB drops
the contract (reports 0), but the latest portfolio snapshot still
carries the 25-contract leg and no code path in the reconcile pipeline
closes out or classifies an EXPIRED local option. The reconciler
therefore reports the dead contract as live quantity drift and pages
until someone manually fixes the snapshot.

These tests encode the exact production shape (expired expiry via
window-relative today-minus-2, IB residual 0-qty row, local 25 x $150 C)
and assert the pipeline classifies it as expired/closed instead of a
paging quantity mismatch. They are RED until expiry-awareness exists.

Harness conventions follow test_position_reconcile_spine.py.
"""

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import ib_reconcile

# Friday-expiry-detected-Sunday in production == today-minus-2.
EXPIRED_EXPIRY_IB = (date.today() - timedelta(days=2)).strftime("%Y%m%d")
EXPIRED_EXPIRY_SNAPSHOT = (date.today() - timedelta(days=2)).strftime("%Y-%m-%d")
LIVE_EXPIRY_IB = (date.today() + timedelta(days=180)).strftime("%Y%m%d")
LIVE_EXPIRY_SNAPSHOT = (date.today() + timedelta(days=180)).strftime("%Y-%m-%d")

SYMBOL = "SPCX"
LOCAL_CONTRACTS = 25
STRIKE = 150.0


def _ib_opt(symbol, qty, strike, right, expiry):
    return {
        "symbol": symbol,
        "sec_type": "OPT",
        "quantity": qty,
        "avg_cost": 1.0,
        "strike": strike,
        "expiry": expiry,
        "right": right,
    }


def _local_position(ticker, contracts, direction, strike, leg_type, expiry):
    return {
        "ticker": ticker,
        "structure": f"{'Long' if direction == 'LONG' else 'Short'} {leg_type} ${strike}",
        "expiry": expiry,
        "contracts": contracts,
        "direction": direction,
        "legs": [
            {
                "direction": direction,
                "contracts": contracts,
                "type": leg_type,
                "strike": strike,
                "expiry": expiry,
            }
        ],
    }


def _production_shape():
    """IB residual 0-qty row for the expired contract; snapshot still long 25."""
    ib_positions = [_ib_opt(SYMBOL, 0, STRIKE, "C", EXPIRED_EXPIRY_IB)]
    portfolio = {
        "positions": [
            _local_position(
                SYMBOL, LOCAL_CONTRACTS, "LONG", STRIKE, "Call", EXPIRED_EXPIRY_SNAPSHOT
            )
        ]
    }
    return ib_positions, portfolio


class TestExpiredOptionNotLiveDrift(unittest.TestCase):
    def test_expired_contract_not_reported_as_quantity_mismatch(self):
        """Expiry < today + IB 0 must classify as expired/closed, not drift."""
        ib_positions, portfolio = _production_shape()
        disc = ib_reconcile.find_position_discrepancies(ib_positions, portfolio)
        self.assertEqual(
            disc["quantity_mismatch"],
            [],
            "expired contract (expiry in the past, IB reports 0) must not be "
            f"classified as live quantity drift: {disc['quantity_mismatch']}",
        )

    def test_live_contract_drift_still_detected(self):
        """Guard: expiry-awareness must not blanket-suppress real drift."""
        ib_positions = [_ib_opt(SYMBOL, 0, STRIKE, "C", LIVE_EXPIRY_IB)]
        portfolio = {
            "positions": [
                _local_position(
                    SYMBOL, LOCAL_CONTRACTS, "LONG", STRIKE, "Call", LIVE_EXPIRY_SNAPSHOT
                )
            ]
        }
        disc = ib_reconcile.find_position_discrepancies(ib_positions, portfolio)
        self.assertEqual(len(disc["quantity_mismatch"]), 1)
        self.assertEqual(disc["quantity_mismatch"][0]["ib_quantity"], 0)
        self.assertEqual(
            disc["quantity_mismatch"][0]["local_quantity"], LOCAL_CONTRACTS
        )


class TestMainHealthOnExpiredOption(unittest.TestCase):
    def test_main_records_ok_when_only_drift_is_expired_contract(self):
        """The Sunday run: expired-only drift must not write state='error'."""
        ib_positions, portfolio = _production_shape()
        client = MagicMock()
        recorded = []
        with patch.object(ib_reconcile, "connect_ib", return_value=client), patch.object(
            ib_reconcile, "load_trade_log", return_value={"trades": []}
        ), patch.object(
            ib_reconcile, "load_portfolio_snapshot", return_value=portfolio
        ), patch.object(
            ib_reconcile, "fetch_ib_executions", return_value=[]
        ), patch.object(
            ib_reconcile, "fetch_ib_positions", return_value=ib_positions
        ), patch.object(
            ib_reconcile, "save_reconciliation_report"
        ), patch.object(
            ib_reconcile, "_record_health", side_effect=lambda s, d: recorded.append((s, d))
        ):
            ib_reconcile.main()
        self.assertEqual(len(recorded), 1)
        state, detail = recorded[0]
        self.assertEqual(
            state,
            "ok",
            "expired-option cleanup must not page: recorded "
            f"state={state!r} detail={detail!r}",
        )


class TestHandlerHealthOnExpiredOption(unittest.TestCase):
    def _make_handler(self, ib_positions, portfolio):
        from monitor_daemon.handlers.position_reconcile import (
            PositionReconcileHandler,
        )

        client = MagicMock()
        handler = PositionReconcileHandler(
            client_factory=MagicMock(return_value=client),
            snapshot_loader=lambda: portfolio,
            positions_fetcher=lambda c: ib_positions,
        )
        handler.record_cycle_health = MagicMock()
        return handler, client

    def test_handler_heartbeat_ok_when_only_drift_is_expired_contract(self):
        ib_positions, portfolio = _production_shape()
        handler, client = self._make_handler(ib_positions, portfolio)
        result = handler.execute()
        state = handler.record_cycle_health.call_args[0][0]
        self.assertEqual(
            state,
            "ok",
            "position-reconcile heartbeat must not latch error on an expired "
            f"contract: state={state!r} result={result!r}",
        )
        self.assertNotIn("error", result)
        client.disconnect.assert_called_once()


if __name__ == "__main__":
    unittest.main()
