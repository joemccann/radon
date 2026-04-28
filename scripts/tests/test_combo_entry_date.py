import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import ib_sync


class TestComboEntryDateResolution(unittest.TestCase):
    def test_multi_leg_option_position_uses_contract_specific_blotter_dates(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            portfolio_path = data_dir / "portfolio.json"
            portfolio_path.write_text(json.dumps({"positions": []}))
            (data_dir / "trade_log.json").write_text(json.dumps({"trades": []}))
            (data_dir / "blotter.json").write_text(
                json.dumps(
                    {
                        "open_trades": [
                            {
                                "symbol": "PLTR  260327C00155000",
                                "sec_type": "OPT",
                                "executions": [{"time": "2026-03-24T10:05:00"}],
                            },
                            {
                                "symbol": "PLTR  260327P00152500",
                                "sec_type": "OPT",
                                "executions": [{"time": "2026-03-24T10:05:01"}],
                            },
                            {
                                "symbol": "PLTR  260327C00145000",
                                "sec_type": "OPT",
                                "executions": [{"time": "2026-03-19T15:52:25"}],
                            },
                        ]
                    }
                )
            )

            collapsed_positions = [
                {
                    "id": 16,
                    "ticker": "PLTR",
                    "structure": "Risk Reversal (P$152.5/C$155.0)",
                    "structure_type": "Risk Reversal",
                    "risk_profile": "undefined",
                    "expiry": "2026-03-27",
                    "contracts": 20,
                    "direction": "COMBO",
                    "entry_cost": -1571.92,
                    "max_risk": None,
                    "market_value": -1760.0,
                    "market_price_is_calculated": False,
                    "ib_daily_pnl": None,
                    "legs": [
                        {
                            "direction": "LONG",
                            "contracts": 20,
                            "type": "Call",
                            "strike": 155.0,
                            "entry_cost": 5034.01,
                            "avg_cost": 251.70045,
                            "market_price": 2.48,
                            "market_value": 4960.0,
                            "market_price_is_calculated": False,
                        },
                        {
                            "direction": "SHORT",
                            "contracts": 20,
                            "type": "Put",
                            "strike": 152.5,
                            "entry_cost": 6605.93,
                            "avg_cost": 330.29626,
                            "market_price": 3.36,
                            "market_value": 6720.0,
                            "market_price_is_calculated": False,
                        },
                    ],
                    "kelly_optimal": None,
                    "target": None,
                    "stop": None,
                }
            ]

            with patch.object(ib_sync, "PORTFOLIO_PATH", portfolio_path):
                result = ib_sync.convert_to_portfolio_format(
                    {"NetLiquidation": 1_000_000},
                    collapsed_positions,
                    {},
                )

            self.assertEqual(result["positions"][0]["entry_date"], "2026-03-24")


    def test_fill_dates_resolve_entry_date_when_blotter_missing(self):
        """When blotter and trade_log have no data for a same-session trade,
        fill_dates from IB fills should resolve entry_date instead of 'unknown'."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            portfolio_path = data_dir / "portfolio.json"
            portfolio_path.write_text(json.dumps({"positions": []}))
            (data_dir / "trade_log.json").write_text(json.dumps({"trades": []}))
            (data_dir / "blotter.json").write_text(json.dumps({"open_trades": []}))

            collapsed_positions = [
                {
                    "id": 1,
                    "ticker": "AAOI",
                    "structure": "Long Put $110.0",
                    "structure_type": "Long Put",
                    "risk_profile": "defined",
                    "expiry": "2026-04-02",
                    "contracts": 25,
                    "direction": "LONG",
                    "entry_cost": 19367.51,
                    "max_risk": None,
                    "market_value": 18000.0,
                    "market_price_is_calculated": False,
                    "ib_daily_pnl": None,
                    "legs": [
                        {
                            "direction": "LONG",
                            "contracts": 25,
                            "type": "Put",
                            "strike": 110.0,
                            "entry_cost": 19367.51,
                            "avg_cost": 774.70,
                            "market_price": 7.20,
                            "market_value": 18000.0,
                            "market_price_is_calculated": False,
                        },
                    ],
                    "kelly_optimal": None,
                    "target": None,
                    "stop": None,
                }
            ]

            fill_dates = {"AAOI|2026-04-02|P|110.0": "2026-03-25"}

            with patch.object(ib_sync, "PORTFOLIO_PATH", portfolio_path):
                result = ib_sync.convert_to_portfolio_format(
                    {"NetLiquidation": 1_000_000},
                    collapsed_positions,
                    {},
                    fill_dates=fill_dates,
                )

            self.assertEqual(result["positions"][0]["entry_date"], "2026-03-25")

    def test_fill_dates_not_used_when_blotter_has_data(self):
        """Blotter dates should take priority over fill_dates."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            portfolio_path = data_dir / "portfolio.json"
            portfolio_path.write_text(json.dumps({"positions": []}))
            (data_dir / "trade_log.json").write_text(json.dumps({"trades": []}))
            (data_dir / "blotter.json").write_text(
                json.dumps(
                    {
                        "open_trades": [
                            {
                                "symbol": "AAOI  260402P00110000",
                                "sec_type": "OPT",
                                "executions": [{"time": "2026-03-24T14:30:00"}],
                            },
                        ]
                    }
                )
            )

            collapsed_positions = [
                {
                    "id": 1,
                    "ticker": "AAOI",
                    "structure": "Long Put $110.0",
                    "structure_type": "Long Put",
                    "risk_profile": "defined",
                    "expiry": "2026-04-02",
                    "contracts": 25,
                    "direction": "LONG",
                    "entry_cost": 19367.51,
                    "max_risk": None,
                    "market_value": 18000.0,
                    "market_price_is_calculated": False,
                    "ib_daily_pnl": None,
                    "legs": [
                        {
                            "direction": "LONG",
                            "contracts": 25,
                            "type": "Put",
                            "strike": 110.0,
                            "entry_cost": 19367.51,
                            "avg_cost": 774.70,
                            "market_price": 7.20,
                            "market_value": 18000.0,
                            "market_price_is_calculated": False,
                        },
                    ],
                    "kelly_optimal": None,
                    "target": None,
                    "stop": None,
                }
            ]

            # fill_dates says today, but blotter says yesterday — blotter wins
            fill_dates = {"AAOI|2026-04-02|P|110.0": "2026-03-25"}

            with patch.object(ib_sync, "PORTFOLIO_PATH", portfolio_path):
                result = ib_sync.convert_to_portfolio_format(
                    {"NetLiquidation": 1_000_000},
                    collapsed_positions,
                    {},
                    fill_dates=fill_dates,
                )

            self.assertEqual(result["positions"][0]["entry_date"], "2026-03-24")

    def test_new_contract_on_existing_ticker_does_not_inherit_older_blotter_date(self):
        """REGRESSION: when the user opens a brand-new position (different
        strikes/expiry) on a ticker that already has older option trades in
        the blotter, entry_date must NOT inherit the unrelated older date.

        Real-world bug: AMD Risk Reversal P$320/C$330 expiry 2026-05-08
        opened today was assigned entry_date 2026-04-22 because the blotter
        had an unrelated AMD 295P 2026-05-01 from that date. The frontend
        same-day branch missed it, the close-based daily P&L kicked in, and
        Today P&L showed wildly wrong numbers."""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            portfolio_path = data_dir / "portfolio.json"
            portfolio_path.write_text(json.dumps({"positions": []}))
            (data_dir / "trade_log.json").write_text(json.dumps({"trades": []}))
            (data_dir / "blotter.json").write_text(
                json.dumps(
                    {
                        "open_trades": [
                            # Older AMD trades — different expiry/strikes from the new RR
                            {
                                "symbol": "AMD   260501P00295000",
                                "sec_type": "OPT",
                                "executions": [{"time": "2026-04-22T15:30:00"}],
                            },
                            {
                                "symbol": "AMD   260618P00270000",
                                "sec_type": "OPT",
                                "executions": [{"time": "2026-04-24T15:30:00"}],
                            },
                        ]
                    }
                )
            )

            # Brand-new AMD Risk Reversal: P$320/C$330 expiry 2026-05-08.
            # Neither leg matches the older blotter contracts.
            collapsed_positions = [
                {
                    "id": 1,
                    "ticker": "AMD",
                    "structure": "Risk Reversal (P$320.0/C$330.0)",
                    "structure_type": "Risk Reversal",
                    "risk_profile": "undefined",
                    "expiry": "2026-05-08",
                    "contracts": 50,
                    "direction": "COMBO",
                    "entry_cost": 2687.0,
                    "max_risk": None,
                    "market_value": 11000.0,
                    "market_price_is_calculated": False,
                    "ib_daily_pnl": None,
                    "legs": [
                        {
                            "direction": "LONG", "contracts": 50, "type": "Call", "strike": 330.0,
                            "entry_cost": 80413.83, "avg_cost": 1608.28, "market_price": 17.20,
                            "market_value": 86000.0, "market_price_is_calculated": False,
                        },
                        {
                            "direction": "SHORT", "contracts": 50, "type": "Put", "strike": 320.0,
                            "entry_cost": 77726.41, "avg_cost": 1554.53, "market_price": 15.00,
                            "market_value": 75000.0, "market_price_is_calculated": False,
                        },
                    ],
                    "kelly_optimal": None, "target": None, "stop": None,
                }
            ]

            with patch.object(ib_sync, "PORTFOLIO_PATH", portfolio_path):
                result = ib_sync.convert_to_portfolio_format(
                    {"NetLiquidation": 1_000_000},
                    collapsed_positions,
                    {},
                )

            entry_date = result["positions"][0]["entry_date"]
            # Must NOT be the older AMD 295P date (would trigger overnight P&L on a same-day trade)
            self.assertNotEqual(entry_date, "2026-04-22")
            self.assertNotEqual(entry_date, "2026-04-24")
            # Brand-new position with no historical record → default to today.
            self.assertEqual(entry_date, today)

    def test_brand_new_position_with_no_history_defaults_to_today(self):
        """Without any blotter / trade_log / fill / prev_portfolio data, a
        position appearing in the current sync must be considered new today
        (so the same-day P&L branch fires correctly), not 'unknown'."""
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")

        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            portfolio_path = data_dir / "portfolio.json"
            portfolio_path.write_text(json.dumps({"positions": []}))
            (data_dir / "trade_log.json").write_text(json.dumps({"trades": []}))
            (data_dir / "blotter.json").write_text(json.dumps({"open_trades": []}))

            collapsed_positions = [
                {
                    "id": 1,
                    "ticker": "AAOI",
                    "structure": "Long Put $110.0",
                    "structure_type": "Long Put",
                    "risk_profile": "defined",
                    "expiry": "2026-04-02",
                    "contracts": 25,
                    "direction": "LONG",
                    "entry_cost": 19367.51,
                    "max_risk": None,
                    "market_value": 18000.0,
                    "market_price_is_calculated": False,
                    "ib_daily_pnl": None,
                    "legs": [
                        {
                            "direction": "LONG", "contracts": 25, "type": "Put", "strike": 110.0,
                            "entry_cost": 19367.51, "avg_cost": 774.70, "market_price": 7.20,
                            "market_value": 18000.0, "market_price_is_calculated": False,
                        },
                    ],
                    "kelly_optimal": None, "target": None, "stop": None,
                }
            ]

            with patch.object(ib_sync, "PORTFOLIO_PATH", portfolio_path):
                result = ib_sync.convert_to_portfolio_format(
                    {"NetLiquidation": 1_000_000},
                    collapsed_positions,
                    {},
                )

            self.assertEqual(result["positions"][0]["entry_date"], today)

    def test_without_fill_dates_falls_back_to_today(self):
        """Without fill_dates and no other source, entry_date defaults to today
        (changed from the legacy 'unknown' value)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            data_dir = Path(tmpdir)
            portfolio_path = data_dir / "portfolio.json"
            portfolio_path.write_text(json.dumps({"positions": []}))
            (data_dir / "trade_log.json").write_text(json.dumps({"trades": []}))
            (data_dir / "blotter.json").write_text(json.dumps({"open_trades": []}))

            collapsed_positions = [
                {
                    "id": 1,
                    "ticker": "AAOI",
                    "structure": "Long Put $110.0",
                    "structure_type": "Long Put",
                    "risk_profile": "defined",
                    "expiry": "2026-04-02",
                    "contracts": 25,
                    "direction": "LONG",
                    "entry_cost": 19367.51,
                    "max_risk": None,
                    "market_value": 18000.0,
                    "market_price_is_calculated": False,
                    "ib_daily_pnl": None,
                    "legs": [
                        {
                            "direction": "LONG",
                            "contracts": 25,
                            "type": "Put",
                            "strike": 110.0,
                            "entry_cost": 19367.51,
                            "avg_cost": 774.70,
                            "market_price": 7.20,
                            "market_value": 18000.0,
                            "market_price_is_calculated": False,
                        },
                    ],
                    "kelly_optimal": None,
                    "target": None,
                    "stop": None,
                }
            ]

            with patch.object(ib_sync, "PORTFOLIO_PATH", portfolio_path):
                result = ib_sync.convert_to_portfolio_format(
                    {"NetLiquidation": 1_000_000},
                    collapsed_positions,
                    {},
                )

            # No fill_dates passed → defaults to today (per "brand-new
            # position must trigger same-day P&L" invariant).
            from datetime import datetime
            today = datetime.now().strftime("%Y-%m-%d")
            self.assertEqual(result["positions"][0]["entry_date"], today)


if __name__ == "__main__":
    unittest.main()
