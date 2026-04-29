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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
