#!/usr/bin/env python3
"""REL-001 — position-reconciliation spine (RELIABILITY_AUDIT.md R-005).

Fault-injection coverage:
  1. Same-symbol quantity drift between IB and the latest portfolio
     snapshot must be detected (find_position_discrepancies) and must
     flip needs_attention.
  2. ib_reconcile.main() must exit non-zero and record an error
     service_health row when IB is unreachable (was: warn + exit 0,
     route reported {"ok": true}).
  3. main() must record a service_health row on every run (ok when
     clean, error when needs_attention).
  4. The PositionReconcileHandler runs the same detection inside the
     monitor daemon: error heartbeat + result["error"] on drift,
     ok when clean, raise (soft failure) when IB is unreachable.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import ib_reconcile


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
            }
        ],
    }


class TestQuantityMismatchDetection(unittest.TestCase):
    def test_same_symbol_quantity_drift_detected(self):
        """IB holds 5, snapshot says 3, same contract → quantity_mismatch."""
        ib_positions = [_ib_opt("AAOI", 5, 10.0, "C", "20260618")]
        portfolio = {
            "positions": [
                _local_position("AAOI", 3, "LONG", 10.0, "Call", "2026-06-18")
            ]
        }
        disc = ib_reconcile.find_position_discrepancies(ib_positions, portfolio)
        self.assertEqual(len(disc["quantity_mismatch"]), 1)
        mismatch = disc["quantity_mismatch"][0]
        self.assertEqual(mismatch["symbol"], "AAOI")
        self.assertEqual(mismatch["ib_quantity"], 5)
        self.assertEqual(mismatch["local_quantity"], 3)
        # Same symbol on both sides — must NOT double-report as missing.
        self.assertEqual(disc["missing_locally"], [])
        self.assertEqual(disc["missing_in_ib"], [])

    def test_matched_book_is_clean(self):
        ib_positions = [_ib_opt("CRCL", 40, 110.0, "C", "20260618")]
        portfolio = {
            "positions": [
                _local_position("CRCL", 40, "LONG", 110.0, "Call", "2026-06-18")
            ]
        }
        disc = ib_reconcile.find_position_discrepancies(ib_positions, portfolio)
        self.assertEqual(disc["quantity_mismatch"], [])
        self.assertEqual(disc["missing_locally"], [])
        self.assertEqual(disc["missing_in_ib"], [])

    def test_short_signs_compare_signed(self):
        """Local SHORT 3 == IB -3 (clean); IB -5 vs local SHORT 3 → drift."""
        portfolio = {
            "positions": [
                _local_position("MU", 3, "SHORT", 90.0, "Put", "2026-09-18")
            ]
        }
        clean = ib_reconcile.find_position_discrepancies(
            [_ib_opt("MU", -3, 90.0, "P", "20260918")], portfolio
        )
        self.assertEqual(clean["quantity_mismatch"], [])

        drift = ib_reconcile.find_position_discrepancies(
            [_ib_opt("MU", -5, 90.0, "P", "20260918")], portfolio
        )
        self.assertEqual(len(drift["quantity_mismatch"]), 1)
        self.assertEqual(drift["quantity_mismatch"][0]["ib_quantity"], -5)
        self.assertEqual(drift["quantity_mismatch"][0]["local_quantity"], -3)

    def test_needs_attention_on_quantity_mismatch_alone(self):
        report = ib_reconcile.generate_reconciliation_report(
            [],
            {
                "missing_locally": [],
                "missing_in_ib": [],
                "quantity_mismatch": [
                    {"symbol": "AAOI", "ib_quantity": 5, "local_quantity": 3}
                ],
            },
        )
        self.assertTrue(report["needs_attention"])
        self.assertEqual(len(report["quantity_mismatch"]), 1)


class TestMainExitAndHealth(unittest.TestCase):
    def test_main_exits_nonzero_and_records_error_when_ib_unreachable(self):
        recorded = []
        with patch.object(ib_reconcile, "connect_ib", return_value=None), patch.object(
            ib_reconcile, "_record_health", side_effect=lambda s, d: recorded.append((s, d))
        ):
            with self.assertRaises(SystemExit) as ctx:
                ib_reconcile.main()
        self.assertNotEqual(ctx.exception.code, 0)
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][0], "error")

    def test_main_records_ok_health_on_clean_run(self):
        client = MagicMock()
        recorded = []
        with patch.object(ib_reconcile, "connect_ib", return_value=client), patch.object(
            ib_reconcile, "load_trade_log", return_value={"trades": []}
        ), patch.object(
            ib_reconcile, "load_portfolio_snapshot", return_value={"positions": []}
        ), patch.object(
            ib_reconcile, "fetch_ib_executions", return_value=[]
        ), patch.object(
            ib_reconcile, "fetch_ib_positions", return_value=[]
        ), patch.object(
            ib_reconcile, "save_reconciliation_report"
        ), patch.object(
            ib_reconcile, "_record_health", side_effect=lambda s, d: recorded.append((s, d))
        ):
            ib_reconcile.main()
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0][0], "ok")

    def test_main_records_error_health_when_attention_needed(self):
        client = MagicMock()
        recorded = []
        ib_positions = [_ib_opt("AAOI", 5, 10.0, "C", "20260618")]
        portfolio = {
            "positions": [
                _local_position("AAOI", 3, "LONG", 10.0, "Call", "2026-06-18")
            ]
        }
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
        self.assertEqual(recorded[0][0], "error")
        self.assertIn("quantity_mismatch_count", recorded[0][1])
        self.assertEqual(recorded[0][1]["quantity_mismatch_count"], 1)


class TestPositionReconcileHandler(unittest.TestCase):
    def _make_handler(self, ib_positions, portfolio, connect_ok=True):
        from monitor_daemon.handlers.position_reconcile import (
            PositionReconcileHandler,
        )

        client = MagicMock()
        factory = MagicMock(return_value=client)
        if not connect_ok:
            factory.side_effect = ConnectionRefusedError("gateway down")
        handler = PositionReconcileHandler(
            client_factory=factory,
            snapshot_loader=lambda: portfolio,
            positions_fetcher=lambda c: ib_positions,
        )
        handler.record_cycle_health = MagicMock()
        return handler, client

    def test_error_heartbeat_and_result_error_on_drift(self):
        handler, client = self._make_handler(
            [_ib_opt("AAOI", 5, 10.0, "C", "20260618")],
            {"positions": [_local_position("AAOI", 3, "LONG", 10.0, "Call", "2026-06-18")]},
        )
        result = handler.execute()
        self.assertIn("error", result)
        state, kwargs = (
            handler.record_cycle_health.call_args[0][0],
            handler.record_cycle_health.call_args[1],
        )
        self.assertEqual(state, "error")
        self.assertEqual(kwargs["error"]["quantity_mismatch_count"], 1)
        client.disconnect.assert_called_once()

    def test_ok_heartbeat_when_clean(self):
        handler, client = self._make_handler(
            [_ib_opt("CRCL", 40, 110.0, "C", "20260618")],
            {"positions": [_local_position("CRCL", 40, "LONG", 110.0, "Call", "2026-06-18")]},
        )
        result = handler.execute()
        self.assertNotIn("error", result)
        self.assertEqual(handler.record_cycle_health.call_args[0][0], "ok")
        client.disconnect.assert_called_once()

    def test_raises_when_ib_unreachable(self):
        handler, _ = self._make_handler([], {"positions": []}, connect_ok=False)
        with self.assertRaises(Exception):
            handler.execute()

    def test_registered_in_production_daemon(self):
        from monitor_daemon import run as daemon_run

        daemon = daemon_run.create_daemon()
        names = [h.name for h in daemon.handlers]
        self.assertIn("position_reconcile", names)


if __name__ == "__main__":
    unittest.main()
