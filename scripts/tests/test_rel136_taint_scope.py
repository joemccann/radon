"""R-383 / R-406 / R-407 / REL-136: the taint is scoped to what it can prove.

R-383: `basis_tainted` is set on ANY same-direction fingerprint suppression, but
the fingerprint is `(contract, date, signed_qty)` — it cannot see price. The
TRUE duplicate the suppression exists for (REL-024 / R-049: one fill written by
the realtime daemon under a dotted API execId and again by Flex rehydrate under
a numeric tradeID) has IDENTICAL cash, and after suppressing it the basis is
exactly right. Withholding there falls back to IB's drifted avgCost figure — the
$11,558 understatement this module exists to replace — on every contract Flex
has rehydrated, which is the normal production state.

R-406: the flags were function-scoped and set-once, so a contract re-entered
after going flat kept the taint forever even though `position_qty == 0` already
resets the basis the taint is a statement about.

R-407: `_is_plausible_contract` checks the expiry is a CALENDAR-valid date, not
that it precedes the row's own fill date, so a field-swap still fabricates P&L.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from clients.journal_realized import realized_pnl_by_exec_id  # noqa: E402

_C60 = {"ticker": "SLV", "strike": 60.0, "right": "C", "expiry": "20261016"}

API_ID = "0000e0d5.68abc123.01.01"
API_ID_2 = "0000e0d5.68abc124.01.01"
FLEX_ID = "9998092102"
FLEX_ID_2 = "9998092103"


def _row(exec_id, action, qty, price, commission, contract, date, written, exec_time=None):
    payload = {
        **contract,
        "action": action,
        "contracts": qty,
        "fill_price": price,
        "commission": commission,
        "total_cost": qty * price * 100 + commission,
        "ib_exec_id": exec_id,
        "date": date,
        "multiplier": 100.0,
    }
    if exec_time:
        payload["execution_time"] = exec_time
    return (json.dumps(payload), date, written)


class TestIdenticalCashSuppressionIsNotATaint:
    def test_a_proven_cross_writer_duplicate_keeps_the_journal_figure(self, caplog):
        """The dual-write shape: same contract, same day, same qty, SAME price."""
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1",
                 "2026-08-07T14:00:00-04:00"),
            _row(FLEX_ID, "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w2",
                 "2026-08-07T15:00:00-04:00"),
            _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w3"),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w4"),
        ]
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(rows)
        # basis is (1.00 + 3.00) / 2 = $2.00/unit -> 10 x ($500 - $200) = $3,000
        assert realized == {"c1": 3000.0}
        assert [r for r in caplog.records if r.levelname == "WARNING"] == []

    def test_two_distinct_partials_at_different_prices_still_withhold(self):
        """The false-positive case the suppression was built for is unchanged.

        Same fingerprint, DIFFERENT cash: the module cannot tell a duplicate from
        two genuinely distinct equal-size same-day partials, so it must still
        under-count rather than publish a basis it cannot stand behind.
        """
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1",
                 "2026-08-07T14:00:00-04:00"),
            _row(FLEX_ID, "BUY_OPTION", 10, 2.00, 0.0, _C60, "2026-08-07", "w2",
                 "2026-08-07T15:00:00-04:00"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {}

    def test_the_dual_written_close_correction_is_unchanged(self):
        rows = [
            _row("o1", "BUY_OPTION", 30, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row(API_ID, "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w2"),
            _row(FLEX_ID, "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3"),
            _row("c2", "SELL_OPTION", 10, 4.00, 0.0, _C60, "2026-08-26", "w4"),
        ]
        assert realized_pnl_by_exec_id(rows) == {API_ID: 2000.0, "c2": 3000.0}


class TestASecondEqualPricePartialIsNotADuplicate:
    """T-316: the identical-cash carve-out covers ONE duplicate per counted fill.

    The fingerprint `(contract, date, signed_qty)` collapses a SECOND equal-price
    Flex partial onto the daemon's one, and R-383 waved it through at `info`
    because the cash matched. But the daemon's row has already been matched by
    the first Flex row; the second has no counted counterpart to duplicate, so
    suppressing it drops a real fill and the inventory is short by one lot.
    """

    _KEY = "SLV|20261016|C|60.0"

    @staticmethod
    def _five_rows():
        """api BUY 10@1, flex BUY 10@1 (dup), flex BUY 10@1 (distinct), BUY 10@3,
        SELL 20@5. True basis (20x1 + 10x3)/30 -> 6,666.67 with 10 still long;
        the short-by-one replay prices it at $2/unit -> 6,000 and goes flat.
        """
        return [
            _row(API_ID, "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1",
                 "2026-08-07T14:00:00-04:00"),
            _row(FLEX_ID, "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w2",
                 "2026-08-07T14:00:00-04:00"),
            _row(FLEX_ID_2, "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w3",
                 "2026-08-07T15:30:00-04:00"),
            _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w4"),
            _row("c1", "SELL_OPTION", 20, 5.00, 0.0, _C60, "2026-08-20", "w5"),
        ]

    def test_the_close_priced_against_the_short_inventory_is_withheld(self, caplog):
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(self._five_rows())
        assert "c1" not in realized, f"published {realized} against a true 6666.67"
        warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
        assert any(self._KEY in message for message in warnings), warnings

    def test_the_taint_reaches_the_close_that_flattens_the_replay(self, caplog):
        """`c1` takes the replay to zero; R-406's reset must apply AFTER it."""
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(self._five_rows())
        assert realized == {}
        assert any("withholding c1" in r.getMessage() for r in caplog.records)

    def test_a_dual_written_pair_of_partials_still_publishes(self, caplog):
        """Two daemon partials + two Flex partials, all equal cash: every Flex
        row has a counted counterpart, so the carve-out holds and no taint."""
        rows = [
            _row(API_ID, "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1",
                 "2026-08-07T14:00:00-04:00"),
            _row(API_ID_2, "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w2",
                 "2026-08-07T14:30:00-04:00"),
            _row(FLEX_ID, "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w3",
                 "2026-08-07T15:00:00-04:00"),
            _row(FLEX_ID_2, "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w4",
                 "2026-08-07T15:30:00-04:00"),
            _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w5"),
            _row("c1", "SELL_OPTION", 30, 5.00, 0.0, _C60, "2026-08-20", "w6"),
        ]
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(rows)
        # basis (2x1.00 + 3.00)/3 = 1.6667/unit -> 30 x (500 - 166.67) = 10,000
        assert realized == {"c1": 10000.0}
        assert [r for r in caplog.records if r.levelname == "WARNING"] == []


class TestFlatPositionClearsTheTaint:
    def test_a_reopened_contract_is_replayed_against_its_own_basis(self):
        """Cycle 1 dual-written at identical cash, cycle 2 clean. Both publish."""
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1",
                 "2026-08-07T14:00:00-04:00"),
            _row(FLEX_ID, "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w2",
                 "2026-08-07T15:00:00-04:00"),
            _row("c1", "SELL_OPTION", 10, 2.00, 0.0, _C60, "2026-08-11", "w3"),
            _row("o2", "BUY_OPTION", 10, 4.00, 0.0, _C60, "2026-08-14", "w4"),
            _row("c2", "SELL_OPTION", 10, 9.00, 0.0, _C60, "2026-08-20", "w5"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 1000.0, "c2": 5000.0}

    def test_only_the_tainted_cycle_is_withheld(self):
        """Cycle 1 suppression has DIFFERENT cash, so only c1 is forfeited."""
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1",
                 "2026-08-07T14:00:00-04:00"),
            _row(FLEX_ID, "BUY_OPTION", 10, 2.00, 0.0, _C60, "2026-08-07", "w2",
                 "2026-08-07T15:00:00-04:00"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w3"),
            _row("o2", "BUY_OPTION", 10, 4.00, 0.0, _C60, "2026-08-14", "w4"),
            _row("c2", "SELL_OPTION", 10, 9.00, 0.0, _C60, "2026-08-20", "w5"),
        ]
        realized = realized_pnl_by_exec_id(rows)
        assert realized == {"c2": 5000.0}


class TestExpiryMustPrecedeNoFill:
    @staticmethod
    def _corrupted(field, value):
        """BUY 10 @ $1 + BUY 10 @ $3 (`field` corrupted) + SELL 10 @ $5.

        True average basis is $2/unit -> $3,000. Replaying only the kept open
        prices the close against $1 -> $4,000.
        """
        broken = _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w2")
        payload = json.loads(broken[0])
        payload[field] = value
        return [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            (json.dumps(payload), broken[1], broken[2]),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]

    def test_an_expiry_before_the_epoch_of_the_trade_refuses(self, caplog):
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(self._corrupted("expiry", "19700101"))
        assert realized == {}, f"fabricated {realized} against a true 3000.0"
        assert any("incomplete" in r.getMessage().lower() for r in caplog.records)

    def test_a_field_swap_writing_an_earlier_fill_date_into_expiry_refuses(self, caplog):
        """An option cannot be filled after it has expired."""
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(self._corrupted("expiry", "20260807"))
        assert realized == {}, f"fabricated {realized} against a true 3000.0"
        assert any("incomplete" in r.getMessage().lower() for r in caplog.records)

    def test_a_same_day_expiry_is_still_plausible(self):
        """0DTE is real: fill date == expiry must not be rejected."""
        zero_dte = {"ticker": "SPY", "strike": 600.0, "right": "C", "expiry": "20260807"}
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, zero_dte, "2026-08-07", "w1"),
            _row("c1", "SELL_OPTION", 10, 2.00, 0.0, zero_dte, "2026-08-07", "w2"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 1000.0}

    def test_the_healthy_triple_is_unchanged(self):
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w2"),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 3000.0}

    def test_the_seven_verified_holding_corruptions_stay_green(self):
        for field, value in (
            ("strike", 0),
            ("strike", "0"),
            ("strike", -60.0),
            ("right", "X"),
            ("right", ""),
            ("expiry", "20261340"),
            ("expiry", "20260230"),
        ):
            assert realized_pnl_by_exec_id(self._corrupted(field, value)) == {}, (field, value)
