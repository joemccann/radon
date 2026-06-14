#!/usr/bin/env python3
"""
Tests for scripts/journal_rehydrate.py — Red/Green TDD.

Verifies:
- Idempotent re-runs (second call yields imported=0)
- Per-execId dedupe
- atomic_save is invoked when new rows arrive
- Append-only — pre-existing rows are preserved untouched
- Legacy (ticker, date, structure) fingerprint catches rows lacking ib_exec_id
- Failure surfaces in the response without mutating trade_log.json
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Path bootstrap mirrors the script under test.
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "trade_blotter"))

from journal_rehydrate import (  # noqa: E402
    rehydrate,
    rehydrate_from_executions,
    _structure_label,
)
from trade_blotter.models import Execution, SecurityType, Side  # noqa: E402
from utils.atomic_io import atomic_save, verified_load  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_execution(
    *,
    exec_id: str,
    symbol: str,
    sec_type: SecurityType,
    side: Side,
    quantity: int,
    price: float,
    commission: float = 0.0,
    strike: float | None = None,
    right: str | None = None,
    expiry: str | None = None,
    when: datetime | None = None,
) -> Execution:
    return Execution(
        exec_id=exec_id,
        time=when or datetime(2026, 4, 10, 10, 0, 0),
        symbol=symbol,
        sec_type=sec_type,
        side=side,
        quantity=Decimal(str(quantity)),
        price=Decimal(str(price)),
        commission=Decimal(str(commission)),
        strike=Decimal(str(strike)) if strike else None,
        right=right,
        expiry=expiry,
    )


@pytest.fixture
def stock_buy() -> Execution:
    return _make_execution(
        exec_id="EX-001",
        symbol="URTY",
        sec_type=SecurityType.STOCK,
        side=Side.BUY,
        quantity=2000,
        price=55.997,
        commission=10.0,
    )


@pytest.fixture
def opt_buy_long_put() -> Execution:
    return _make_execution(
        exec_id="EX-OPT-1",
        symbol="EWY",
        sec_type=SecurityType.OPTION,
        side=Side.BUY,
        quantity=25,
        price=2.0,
        commission=7.55,
        strike=130,
        right="P",
        expiry="20260313",
    )


@pytest.fixture
def opt_sell_short_call() -> Execution:
    return _make_execution(
        exec_id="EX-OPT-2",
        symbol="EWY",
        sec_type=SecurityType.OPTION,
        side=Side.SELL,
        quantity=25,
        price=1.5,
        commission=7.55,
        strike=141,
        right="C",
        expiry="20260313",
    )


@pytest.fixture
def trade_log_path(tmp_path: Path) -> Path:
    """Disposable trade_log.json with a single legacy row (no ib_exec_id)."""
    path = tmp_path / "trade_log.json"
    atomic_save(
        str(path),
        {
            "trades": [
                {
                    "id": 1,
                    "date": "2026-03-02",
                    "ticker": "ALAB",
                    "structure": "Long Call - LEAP",
                    "decision": "EXECUTED",
                    "action": "TRADE",
                    "fill_price": 36.9,
                    "total_cost": 18452.24,
                    "contracts": 5,
                }
            ]
        },
    )
    return path


# ---------------------------------------------------------------------------
# Pure-function tests
# ---------------------------------------------------------------------------


class TestRehydrateFromExecutions:
    """rehydrate_from_executions() merges Flex executions into existing log."""

    def test_appends_new_stock_trade_with_exec_id(self, stock_buy):
        existing = {"trades": []}
        updated, imported, skipped, latest = rehydrate_from_executions([stock_buy], existing)

        assert imported == 1
        assert skipped == 0
        assert latest == "2026-04-10"

        added = updated["trades"][0]
        assert added["ticker"] == "URTY"
        assert added["action"] == "BUY"
        assert added["shares"] == 2000
        assert added["ib_exec_id"] == "EX-001"
        assert added["decision"] == "IB_AUTO_IMPORT"

    def test_appends_option_trade_with_contract_details(self, opt_buy_long_put):
        existing = {"trades": []}
        updated, imported, _, _ = rehydrate_from_executions([opt_buy_long_put], existing)

        assert imported == 1
        added = updated["trades"][0]
        assert added["ib_exec_id"] == "EX-OPT-1"
        assert added["contracts"] == 25
        assert added["right"] == "P"
        assert added["strike"] == 130.0
        assert added["expiry"] == "20260313"
        assert "Put" in added["structure"]
        assert "$130" in added["structure"]

    def test_idempotent_on_rerun(self, stock_buy):
        existing = {"trades": []}
        first, imported_1, _, _ = rehydrate_from_executions([stock_buy], existing)
        assert imported_1 == 1

        # Second pass with the SAME executions and the now-populated log.
        _, imported_2, skipped_2, _ = rehydrate_from_executions([stock_buy], first)
        assert imported_2 == 0
        assert skipped_2 == 1

    def test_dedup_on_existing_exec_id(self, stock_buy):
        existing = {
            "trades": [
                {
                    "id": 99,
                    "date": "2026-04-10",
                    "ticker": "URTY",
                    "structure": "Long Stock (STK)",
                    "decision": "IB_AUTO_IMPORT",
                    "action": "BUY",
                    "ib_exec_id": "EX-001",
                    "shares": 2000,
                }
            ]
        }
        _, imported, skipped, _ = rehydrate_from_executions([stock_buy], existing)
        assert imported == 0
        assert skipped == 1

    def test_legacy_dedup_without_exec_id(self, opt_buy_long_put):
        # Pre-existing row matches by (ticker, date, structure) but lacks ib_exec_id.
        existing = {
            "trades": [
                {
                    "id": 7,
                    "date": "2026-04-10",
                    "ticker": "EWY",
                    "structure": "Long Put $130 2026-03-13",
                    "decision": "IB_AUTO_IMPORT",
                    "action": "BUY_OPTION",
                    "contracts": 25,
                }
            ]
        }
        _, imported, skipped, _ = rehydrate_from_executions([opt_buy_long_put], existing)
        assert imported == 0
        assert skipped == 1

    def test_collar_legs_stay_separate(self, opt_buy_long_put, opt_sell_short_call):
        existing = {"trades": []}
        updated, imported, _, _ = rehydrate_from_executions(
            [opt_buy_long_put, opt_sell_short_call], existing
        )
        assert imported == 2

        ewy_rows = [t for t in updated["trades"] if t["ticker"] == "EWY"]
        strikes = sorted(t.get("strike") for t in ewy_rows)
        assert strikes == [130.0, 141.0]
        assert {t["action"] for t in ewy_rows} == {"BUY_OPTION", "SELL_TO_OPEN"}

    def test_sell_closing_prior_long_labels_as_sell_option(self):
        """Regression for 2026-05-22 mislabel bug. Pure-sell bucket whose
        prior position was long must label as SELL_OPTION (close long),
        not SELL_TO_OPEN (open short). fromJournal.ts treats those two
        differently for isOpen / net_quantity attribution."""
        # Existing journal row: bought 65 USAX $45 calls earlier.
        existing = {
            "trades": [
                {
                    "id": 1,
                    "date": "2026-04-15",
                    "ticker": "USAX",
                    "structure": "Long Call $45 2026-06-19",
                    "decision": "IB_AUTO_IMPORT",
                    "action": "BUY_OPTION",
                    "contracts": 65,
                    "strike": 45.0,
                    "right": "C",
                    "expiry": "20260619",
                    "ib_exec_id": "PRIOR-BUY",
                }
            ]
        }
        # Today's Flex pull: pure sell of all 65 — must read as a close.
        sell_to_close = _make_execution(
            exec_id="CLOSE-SELL",
            symbol="USAX",
            sec_type=SecurityType.OPTION,
            side=Side.SELL,
            quantity=65,
            price=4.0,
            strike=45.0,
            right="C",
            expiry="20260619",
            when=datetime(2026, 5, 22, 14, 0, 0),
        )
        updated, imported, _, _ = rehydrate_from_executions([sell_to_close], existing)
        assert imported == 1
        new_row = next(t for t in updated["trades"] if t["ib_exec_id"] == "CLOSE-SELL")
        assert new_row["action"] == "SELL_OPTION", (
            f"Expected SELL_OPTION (close long), got {new_row['action']}. "
            f"This regression bites fromJournal.ts: SELL_TO_OPEN sets "
            f"isOpen=true with net_quantity=-65 (phantom new short), "
            f"SELL_OPTION sets isOpen=false with net_quantity=0 (correct)."
        )

    def test_sell_with_no_prior_position_still_labels_sell_to_open(self):
        """Counterpoint: when there's no prior long, a pure sell IS opening
        a short. The label should remain SELL_TO_OPEN."""
        existing = {"trades": []}
        sell_to_open = _make_execution(
            exec_id="SHORT-OPEN",
            symbol="NEWNAME",
            sec_type=SecurityType.OPTION,
            side=Side.SELL,
            quantity=10,
            price=2.5,
            strike=100.0,
            right="P",
            expiry="20260919",
        )
        updated, imported, _, _ = rehydrate_from_executions([sell_to_open], existing)
        assert imported == 1
        assert updated["trades"][0]["action"] == "SELL_TO_OPEN"

    def test_preserves_existing_rows(self, stock_buy):
        existing = {
            "trades": [
                {"id": 1, "date": "2025-12-01", "ticker": "AAA", "structure": "X"},
                {"id": 2, "date": "2025-12-02", "ticker": "BBB", "structure": "Y"},
            ]
        }
        updated, imported, _, _ = rehydrate_from_executions([stock_buy], existing)
        assert imported == 1
        assert len(updated["trades"]) == 3
        assert updated["trades"][0]["ticker"] == "AAA"
        assert updated["trades"][1]["ticker"] == "BBB"
        assert updated["trades"][2]["ticker"] == "URTY"
        # IDs stay monotonic
        assert updated["trades"][2]["id"] == 3

    def test_multi_fill_groups_into_composite_exec_id(self):
        fill_a = _make_execution(
            exec_id="A",
            symbol="WULF",
            sec_type=SecurityType.OPTION,
            side=Side.BUY,
            quantity=8,
            price=5.20,
            commission=0,
            strike=17,
            right="C",
            expiry="20270115",
        )
        fill_b = _make_execution(
            exec_id="B",
            symbol="WULF",
            sec_type=SecurityType.OPTION,
            side=Side.BUY,
            quantity=69,
            price=5.20,
            commission=0,
            strike=17,
            right="C",
            expiry="20270115",
        )

        existing = {"trades": []}
        updated, imported, _, _ = rehydrate_from_executions([fill_a, fill_b], existing)

        assert imported == 1
        row = updated["trades"][0]
        assert row["contracts"] == 77
        assert row["ib_exec_id"] == "A+B"


# ---------------------------------------------------------------------------
# End-to-end (with mocked FlexQueryFetcher) tests
# ---------------------------------------------------------------------------


class TestRehydrateEntryPoint:
    """rehydrate() — atomic file write, idempotency, error handling."""

    def test_writes_atomically_when_new_rows_arrive(self, tmp_path, trade_log_path, stock_buy):
        fetcher = MagicMock()
        fetcher.fetch_executions.return_value = [stock_buy]

        with patch("journal_rehydrate.atomic_save", wraps=atomic_save) as spy:
            result = rehydrate(days=365, trade_log_path=trade_log_path, fetcher=fetcher)

        assert result["ok"] is True
        assert result["imported"] == 1
        assert spy.called

        loaded = verified_load(str(trade_log_path))
        tickers = [t["ticker"] for t in loaded["trades"]]
        assert "ALAB" in tickers  # legacy row preserved
        assert "URTY" in tickers  # new row appended

    def test_does_not_rewrite_when_nothing_new(self, trade_log_path, stock_buy):
        fetcher = MagicMock()
        fetcher.fetch_executions.return_value = [stock_buy]

        # First pass writes.
        rehydrate(days=365, trade_log_path=trade_log_path, fetcher=fetcher)

        # Second pass should be a no-op write: imported=0, skipped=1.
        with patch("journal_rehydrate.atomic_save") as spy:
            result = rehydrate(days=365, trade_log_path=trade_log_path, fetcher=fetcher)

        assert result["imported"] == 0
        assert result["skipped"] == 1
        assert not spy.called

    def test_flex_failure_does_not_touch_trade_log(self, trade_log_path):
        before = verified_load(str(trade_log_path))

        fetcher = MagicMock()
        fetcher.fetch_executions.side_effect = RuntimeError("Flex Query timed out")

        result = rehydrate(days=365, trade_log_path=trade_log_path, fetcher=fetcher)

        assert result["ok"] is False
        assert "Flex" in result["error"]

        after = verified_load(str(trade_log_path))
        assert before == after

    def test_creates_file_when_missing(self, tmp_path, stock_buy):
        target = tmp_path / "fresh_trade_log.json"
        assert not target.exists()

        fetcher = MagicMock()
        fetcher.fetch_executions.return_value = [stock_buy]

        result = rehydrate(days=365, trade_log_path=target, fetcher=fetcher)

        assert result["ok"] is True
        assert result["imported"] == 1
        assert target.exists()
        loaded = verified_load(str(target))
        assert len(loaded["trades"]) == 1


class TestStockRoundTripPnl:
    """_compute_pnl_summary populates realized_pnl/cost_basis/proceeds for stocks.

    Regression: rehydrated stock round-trips were missing the lot-matched
    P&L fields the legacy Flex 1422766 blotter produced, so the
    journal-derived /api/blotter view diverged on every closed equity
    trade. See journal_rehydrate._compute_pnl_summary.
    """

    def test_closed_stock_long_round_trip_with_profit(self):
        buy = _make_execution(
            exec_id="STK-PROFIT-BUY",
            symbol="NVD",
            sec_type=SecurityType.STOCK,
            side=Side.BUY,
            quantity=15000,
            price=6.88,
            commission=75.33,
            when=datetime(2025, 10, 29, 14, 14, 46),
        )
        sell = _make_execution(
            exec_id="STK-PROFIT-SELL",
            symbol="NVD",
            sec_type=SecurityType.STOCK,
            side=Side.SELL,
            quantity=15000,
            price=7.05,
            commission=77.82,
            when=datetime(2025, 10, 31, 15, 36, 17),
        )
        updated, imported, _, _ = rehydrate_from_executions([buy, sell], {"trades": []})
        assert imported == 1
        row = updated["trades"][0]
        assert row["action"] == "CLOSED"
        assert row["realized_quantity"] == 15000
        assert row["total_round_trip_quantity"] == 15000
        # cost_basis = buy.notional + buy.commission = 15000*6.88 + 75.33
        assert row["cost_basis"] == pytest.approx(103275.33, abs=0.01)
        # proceeds = sell.notional - sell.commission = 15000*7.05 - 77.82
        assert row["proceeds"] == pytest.approx(105672.18, abs=0.01)
        assert row["realized_pnl"] == pytest.approx(2396.85, abs=0.05)

    def test_closed_stock_long_round_trip_with_loss(self):
        buy = _make_execution(
            exec_id="STK-LOSS-BUY",
            symbol="ILF",
            sec_type=SecurityType.STOCK,
            side=Side.BUY,
            quantity=2000,
            price=37.16,
            commission=10.0,
            when=datetime(2026, 3, 5, 10, 0, 0),
        )
        sell = _make_execution(
            exec_id="STK-LOSS-SELL",
            symbol="ILF",
            sec_type=SecurityType.STOCK,
            side=Side.SELL,
            quantity=2000,
            price=33.77,
            commission=10.39,
            when=datetime(2026, 3, 9, 10, 0, 0),
        )
        updated, imported, _, _ = rehydrate_from_executions([buy, sell], {"trades": []})
        assert imported == 1
        row = updated["trades"][0]
        assert row["action"] == "CLOSED"
        # cost_basis = 2000*37.16 + 10 = 74330; proceeds = 2000*33.77 - 10.39 = 67529.61
        assert row["cost_basis"] == pytest.approx(74330.0, abs=0.01)
        assert row["proceeds"] == pytest.approx(67529.61, abs=0.01)
        assert row["realized_pnl"] == pytest.approx(-6800.39, abs=0.05)
        assert row["realized_pnl"] < 0

    def test_multi_fill_stock_round_trip(self):
        # Three buys at different prices, then one sell. Average-cost
        # accounting realizes against the rolling average.
        buys = [
            _make_execution(
                exec_id=f"MFB-{i}",
                symbol="URTY",
                sec_type=SecurityType.STOCK,
                side=Side.BUY,
                quantity=qty,
                price=price,
                commission=cmm,
                when=datetime(2026, 4, 1, 9, 30 + i, 0),
            )
            for i, (qty, price, cmm) in enumerate([
                (500, 50.00, 2.50),
                (1000, 51.00, 5.10),
                (500, 52.00, 2.60),
            ])
        ]
        sell = _make_execution(
            exec_id="MFB-SELL",
            symbol="URTY",
            sec_type=SecurityType.STOCK,
            side=Side.SELL,
            quantity=2000,
            price=53.00,
            commission=10.60,
            when=datetime(2026, 4, 2, 14, 0, 0),
        )
        updated, imported, _, _ = rehydrate_from_executions(buys + [sell], {"trades": []})
        assert imported == 1
        row = updated["trades"][0]
        # cost_basis = 500*50 + 2.5 + 1000*51 + 5.1 + 500*52 + 2.6
        #            = 25002.5 + 51005.1 + 26002.6 = 102010.2
        assert row["cost_basis"] == pytest.approx(102010.2, abs=0.01)
        # proceeds = 2000*53 - 10.6 = 105989.4
        assert row["proceeds"] == pytest.approx(105989.4, abs=0.01)
        assert row["realized_pnl"] == pytest.approx(105989.4 - 102010.2, abs=0.05)
        assert row["realized_quantity"] == 2000
        assert row["total_round_trip_quantity"] == 2000

    def test_partial_close_stock_some_still_open(self):
        # 1000-share buy, 400-share partial close. 600 still open.
        buy = _make_execution(
            exec_id="PC-BUY",
            symbol="MSFT",
            sec_type=SecurityType.STOCK,
            side=Side.BUY,
            quantity=1000,
            price=100.00,
            commission=5.00,
            when=datetime(2026, 4, 5, 9, 30, 0),
        )
        partial_sell = _make_execution(
            exec_id="PC-SELL",
            symbol="MSFT",
            sec_type=SecurityType.STOCK,
            side=Side.SELL,
            quantity=400,
            price=110.00,
            commission=2.20,
            when=datetime(2026, 4, 6, 10, 0, 0),
        )
        updated, imported, _, _ = rehydrate_from_executions(
            [buy, partial_sell], {"trades": []}
        )
        assert imported == 1
        row = updated["trades"][0]
        # Net qty > 0, so action should be BUY (still open as long).
        assert row["action"] == "BUY"
        # Lot match: 400 shares closed at avg basis 100.005 -> realized_pnl
        # = 400 * (110 - 100.005) - 2.20 = 3998 - 2.20 - rounding
        assert row["realized_quantity"] == 400
        assert row["realized_pnl"] == pytest.approx(3995.8, abs=0.5)
        # cost_basis is sum of buy notional+commission = 100000+5 = 100005
        assert row["cost_basis"] == pytest.approx(100005.0, abs=0.01)
        # proceeds is sell notional-comm = 44000-2.20 = 43997.80
        assert row["proceeds"] == pytest.approx(43997.8, abs=0.01)
        # Deriver shouldn't crash on this row.
        # No assertion needed — this confirms partial-close serializes safely.

    def test_short_stock_round_trip_pure_close(self):
        sell_short = _make_execution(
            exec_id="SHORT-OPEN",
            symbol="TSLA",
            sec_type=SecurityType.STOCK,
            side=Side.SELL,
            quantity=200,
            price=300.00,
            commission=1.00,
            when=datetime(2026, 4, 1, 10, 0, 0),
        )
        cover = _make_execution(
            exec_id="SHORT-COVER",
            symbol="TSLA",
            sec_type=SecurityType.STOCK,
            side=Side.BUY,
            quantity=200,
            price=280.00,
            commission=0.95,
            when=datetime(2026, 4, 3, 14, 0, 0),
        )
        updated, imported, _, _ = rehydrate_from_executions(
            [sell_short, cover], {"trades": []}
        )
        assert imported == 1
        row = updated["trades"][0]
        assert row["action"] == "CLOSED"
        # Short P&L: short proceeds 200*300-1 = 59999 → cover cost 200*280+0.95 = 56000.95
        # realized_pnl ≈ 59999 - 56000.95 = 3998.05
        assert row["realized_pnl"] == pytest.approx(3998.05, abs=0.5)
        assert row["realized_quantity"] == 200
        assert row["cost_basis"] == pytest.approx(56000.95, abs=0.01)
        assert row["proceeds"] == pytest.approx(59999.0, abs=0.01)

    def test_pnl_fields_idempotent_on_rerun(self):
        # Running rehydrate twice must not double-count cost_basis/proceeds
        # — the second pass dedupes by ib_exec_id and the row stays put.
        buy = _make_execution(
            exec_id="IDEM-1",
            symbol="GOOGL",
            sec_type=SecurityType.STOCK,
            side=Side.BUY,
            quantity=100,
            price=180.00,
            commission=0.50,
            when=datetime(2026, 4, 10, 10, 0, 0),
        )
        sell = _make_execution(
            exec_id="IDEM-2",
            symbol="GOOGL",
            sec_type=SecurityType.STOCK,
            side=Side.SELL,
            quantity=100,
            price=185.00,
            commission=0.55,
            when=datetime(2026, 4, 11, 14, 0, 0),
        )
        first_payload, imported_1, _, _ = rehydrate_from_executions(
            [buy, sell], {"trades": []}
        )
        assert imported_1 == 1
        captured = first_payload["trades"][0].copy()

        # Second pass — same fills, the row already exists.
        second_payload, imported_2, skipped_2, _ = rehydrate_from_executions(
            [buy, sell], first_payload
        )
        assert imported_2 == 0
        assert skipped_2 == 1
        assert second_payload["trades"][0] == captured

    def test_closed_option_round_trip_emits_pnl_fields(self):
        # Same fix applies to options — rehydrated option round-trips
        # should also carry cost_basis / proceeds / realized_pnl.
        buy = _make_execution(
            exec_id="OPT-RT-BUY",
            symbol="AAOI",
            sec_type=SecurityType.OPTION,
            side=Side.BUY,
            quantity=50,
            price=7.40,
            commission=2.50,
            strike=155,
            right="C",
            expiry="20260501",
            when=datetime(2026, 4, 25, 10, 0, 0),
        )
        sell = _make_execution(
            exec_id="OPT-RT-SELL",
            symbol="AAOI",
            sec_type=SecurityType.OPTION,
            side=Side.SELL,
            quantity=50,
            price=7.57,
            commission=2.92,
            strike=155,
            right="C",
            expiry="20260501",
            when=datetime(2026, 4, 27, 14, 0, 0),
        )
        updated, imported, _, _ = rehydrate_from_executions([buy, sell], {"trades": []})
        assert imported == 1
        row = updated["trades"][0]
        # cost_basis = 50*7.40*100 + 2.50 = 37002.50
        assert row["cost_basis"] == pytest.approx(37002.5, abs=0.01)
        # proceeds = 50*7.57*100 - 2.92 = 37847.08
        assert row["proceeds"] == pytest.approx(37847.08, abs=0.01)
        assert row["realized_pnl"] == pytest.approx(844.58, abs=0.5)
        assert row["realized_quantity"] == 50


class TestStructureLabel:
    """Rehydrate's structure label must match journal_sync: a sold-to-open
    short call is "Short Call", a close-long sell stays "Closed Call"."""

    def test_sell_to_open_is_short(self):
        assert _structure_label("SELL_TO_OPEN", "OPT", 215, "C", "2026-07-17") == "Short Call $215 2026-07-17"

    def test_sell_option_close_long_stays_closed(self):
        assert _structure_label("SELL_OPTION", "OPT", 215, "C", "2026-07-17") == "Closed Call $215 2026-07-17"

    def test_closed_roundtrip_stays_closed(self):
        assert _structure_label("CLOSED", "OPT", 215, "C", "2026-07-17") == "Closed Call $215 2026-07-17"

    def test_buy_option_is_long(self):
        assert _structure_label("BUY_OPTION", "OPT", 1000, "C", "2026-06-12") == "Long Call $1000 2026-06-12"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
