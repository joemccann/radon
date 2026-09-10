#!/usr/bin/env python3
"""Journal average-cost realized P&L for executed option fills.

IB's commission-report ``realizedPNL`` is computed against IB's position
avgCost, which drifts after a partial close (SLV 2026-08-24: IB reported
+$18,511 on a 250-lot close whose average-cost P&L was +$30,069). The
``/orders`` endpoint replaces it with the journal-derived figure whenever the
journal holds a complete, per-fill history for the contract.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS_DIR))

from clients.journal_realized import (  # noqa: E402
    _unusable_fill_key,
    apply_journal_realized_pnl,
    journal_realized_pnl_for_fills,
    overlay_journal_realized_pnl,
    realized_pnl_by_exec_id,
)

_C60 = {"ticker": "SLV", "strike": 60.0, "right": "C", "expiry": "20261016"}
_C70 = {"ticker": "SLV", "strike": 70.0, "right": "C", "expiry": "20261016"}


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


def _fill(exec_id, symbol, strike, right, side, qty, ib_pnl):
    return {
        "execId": exec_id,
        "symbol": f"{symbol} {right}{int(strike)}",
        "contract": {
            "symbol": symbol,
            "secType": "OPT",
            "strike": strike,
            "right": right,
            "expiry": "2026-10-16",
        },
        "side": side,
        "quantity": qty,
        "realizedPNL": ib_pnl,
    }


class TestRealizedByExecId:
    def test_long_leg_closes_against_average_cost_basis(self):
        # 10 @ 1.00 (+$1 comm) and 10 @ 2.00 (+$1 comm): $3,002 over 20 = $150.10/contract.
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 1.0, _C60, "2026-08-07", "w1"),
            _row("o2", "BUY_OPTION", 10, 2.00, 1.0, _C60, "2026-08-11", "w2"),
            _row("c1", "SELL_OPTION", 5, 3.00, 0.5, _C60, "2026-08-20", "w3"),
            _row("c2", "SELL_OPTION", 15, 3.00, 1.5, _C60, "2026-08-24", "w4"),
        ]
        realized = realized_pnl_by_exec_id(rows)
        # c1: proceeds 1,500 - 0.50 = 1,499.50 minus 5 x 150.10 = 750.50
        assert realized["c1"] == 749.0
        # c2: proceeds 4,500 - 1.50 = 4,498.50 minus 15 x 150.10 = 2,251.50
        assert realized["c2"] == 2247.0
        assert "o1" not in realized and "o2" not in realized

    def test_short_leg_cover_realizes_against_net_credit(self):
        rows = [
            _row("s1", "SELL_TO_OPEN", 10, 2.00, 1.0, _C70, "2026-08-07", "w1"),
            _row("b1", "BUY_TO_CLOSE", 10, 1.00, 1.0, _C70, "2026-08-24", "w2"),
        ]
        # credit 2,000 - 1 = 1,999; cover 1,000 + 1 = 1,001
        assert realized_pnl_by_exec_id(rows) == {"b1": 998.0}

    def test_partial_close_does_not_drift_the_remaining_basis(self):
        rows = [
            _row("o1", "BUY_OPTION", 20, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-20", "w2"),
            _row("c2", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3"),
        ]
        realized = realized_pnl_by_exec_id(rows)
        assert realized["c1"] == 2000.0
        assert realized["c2"] == 2000.0

    def test_close_without_journaled_open_marks_contract_incomplete(self):
        rows = [
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w1"),
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-24", "w2", "2026-08-24T19:00:00+00:00"),
            _row("c2", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3", "2026-08-24T19:30:00+00:00"),
        ]
        assert realized_pnl_by_exec_id(rows) == {}

    def test_over_close_marks_contract_incomplete(self):
        rows = [
            _row("o1", "BUY_OPTION", 5, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w2"),
        ]
        assert realized_pnl_by_exec_id(rows) == {}

    def test_same_day_rows_order_by_execution_time(self):
        rows = [
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w1", "2026-08-24T19:30:00+00:00"),
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-24", "w2", "2026-08-24T14:00:00+00:00"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 2000.0}

    def test_composite_rehydrate_row_is_counted_once_and_not_attributed(self):
        rows = [
            _row("a+b", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("a", "BUY_OPTION", 5, 1.00, 0.0, _C60, "2026-08-07", "w2"),
            _row("b", "BUY_OPTION", 5, 1.00, 0.0, _C60, "2026-08-07", "w3"),
            _row("c1", "SELL_OPTION", 10, 2.00, 0.0, _C60, "2026-08-24", "w4"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 1000.0}

    # TEST_AUDIT T-124: a rehydrated CLOSED row is a round trip, not a
    # directional fill. `_signed_qty` maps it to -qty, so the replay read it
    # as an opening SHORT and stamped a realized figure onto the next BUY —
    # a re-entry OPEN rendered as a +$998 close on /orders.
    def test_closed_round_trip_row_is_not_an_opening_short(self):
        rows = [
            _row("a+b", "CLOSED", 10, 2.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-24", "w2"),
        ]
        assert realized_pnl_by_exec_id(rows) == {}

    # TEST_AUDIT T-124: the same close journaled by the real-time daemon
    # (IB API execId) and by Flex rehydrate (numeric tradeID) is one fill.
    # Counted twice it over-closes the position and the whole contract falls
    # back to IB's drifted figure — on exactly the fills c09fc347 fixed.
    def test_same_close_under_api_and_flex_ids_counts_once(self):
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("e2", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w2"),
            _row("777", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"e2": 2000.0}

    def test_two_equal_same_day_partials_from_one_writer_both_count(self):
        rows = [
            _row("o1", "BUY_OPTION", 20, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w2"),
            _row("c2", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 2000.0, "c2": 2000.0}

    def test_bag_and_stock_rows_are_ignored(self):
        rows = [
            (json.dumps({"ticker": "SLV", "action": "BUY_OPTION", "contracts": 10,
                         "fill_price": 2.0, "commission": 0.0, "total_cost": 2000.0,
                         "ib_exec_id": "bag1", "right": "?", "date": "2026-08-07"}),
             "2026-08-07", "w1"),
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w2"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 2000.0}


class TestApplyToFills:
    def test_journal_value_replaces_ib_and_keeps_the_ib_figure(self):
        fills = [_fill("c1", "SLV", 60.0, "C", "SLD", 10, 1234.56)]
        apply_journal_realized_pnl(fills, {"c1": 2000.0})
        assert fills[0]["realizedPNL"] == 2000.0
        assert fills[0]["ibRealizedPNL"] == 1234.56
        assert fills[0]["realizedPNLSource"] == "journal"

    def test_fill_missing_from_journal_keeps_ib_value(self):
        fills = [_fill("zz", "SLV", 60.0, "C", "SLD", 10, 1234.56)]
        apply_journal_realized_pnl(fills, {"c1": 2000.0})
        assert fills[0]["realizedPNL"] == 1234.56
        assert fills[0]["realizedPNLSource"] == "ib"
        assert "ibRealizedPNL" not in fills[0]

    def test_opening_fill_is_untouched(self):
        fills = [_fill("o1", "SLV", 60.0, "C", "BOT", 10, None)]
        apply_journal_realized_pnl(fills, {})
        assert fills[0]["realizedPNL"] is None
        assert "realizedPNLSource" not in fills[0]


class TestLoadForFills:
    def test_queries_only_option_fill_tickers(self):
        calls = []
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w2"),
        ]

        class _RecordingDb(_FakeDb):
            def execute(_self, sql, args=()):
                calls.append((sql, tuple(args)))
                return _FakeDb.execute(_self, sql, args)

        fills = [
            _fill("c1", "SLV", 60.0, "C", "SLD", 10, 1.0),
            {"execId": "stk", "contract": {"symbol": "AAPL", "secType": "STK"}, "side": "BOT",
             "quantity": 5, "realizedPNL": None},
        ]
        assert journal_realized_pnl_for_fills(_RecordingDb(rows=rows), fills) == {"c1": 2000.0}
        # One full page ends the walk; only the option ticker is bound.
        assert len(calls) == 1
        assert "SLV" in calls[0][1]
        assert "AAPL" not in calls[0][1]

    def test_no_option_fills_skips_the_query(self):
        class _Refuse:
            def execute(_self, sql, args=()):
                raise AssertionError("must not query")

        assert journal_realized_pnl_for_fills(_Refuse(), []) == {}


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _FakeDb:
    """Paging reader surface the keyset pager drives (R-203).

    Rows are supplied in the legacy ``(payload, filled_at, written_at)`` shape
    and stamped with a synthetic ascending ``trade_id`` here, matching the
    four columns ``_fetch_journal_rows_for_tickers`` selects.
    """

    def __init__(self, rows=None, error=None):
        self._rows = [
            (f"t{index:04d}", *row) for index, row in enumerate(rows or [])
        ]
        self._error = error

    def execute(self, sql, args=()):
        if self._error:
            raise self._error
        cursor = args[0]
        limit = int(args[-1])
        return _FakeCursor([row for row in self._rows if row[0] > cursor][:limit])


class TestOverlayWithDb:
    def test_closed_row_does_not_stamp_the_reentry_open(self):
        db = _FakeDb(rows=[
            _row("a+b", "CLOSED", 10, 2.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-24", "w2"),
        ])
        fills = [_fill("o1", "SLV", 60.0, "C", "BOT", 10, None)]
        overlay_journal_realized_pnl(fills, reader=db)
        assert fills[0]["realizedPNL"] is None
        assert "realizedPNLSource" not in fills[0]
        assert "ibRealizedPNL" not in fills[0]

    def test_persists_journal_figure_on_the_fill_payload(self):
        db = _FakeDb(rows=[
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w2"),
        ])
        fills = [_fill("c1", "SLV", 60.0, "C", "SLD", 10, 1234.5)]
        overlay_journal_realized_pnl(fills, reader=db)
        assert fills[0]["realizedPNL"] == 2000.0
        assert fills[0]["ibRealizedPNL"] == 1234.5
        assert fills[0]["realizedPNLSource"] == "journal"

    def test_journal_read_failure_keeps_ib_figure(self):
        db = _FakeDb(error=RuntimeError("stream not found"))
        fills = [_fill("c1", "SLV", 60.0, "C", "SLD", 10, 1234.5)]
        overlay_journal_realized_pnl(fills, reader=db)
        assert fills[0]["realizedPNL"] == 1234.5
        assert "ibRealizedPNL" not in fills[0]


class TestDroppedRowCompleteness:
    """R-198: a row dropped for a non-quantity reason must not be invisible.

    Both existing guards are quantity-only. Dropping a row removes its
    quantity AND its cost together, so the two errors cancel exactly and the
    replay fabricates realized P&L against a basis that is too low, with no
    warning. The overlay then persists it into ``executed_orders.payload``.
    """

    def test_null_fill_price_open_refuses_instead_of_overstating(self, caplog):
        # BUY 10 @ $1 (kept), BUY 10 @ $3 (dropped: fill_price null),
        # SELL 10 @ $5. True average basis is $2/unit -> pnl $3,000.
        # Replaying only the kept open gives a $1 basis -> $4,000, and the
        # `qty (10) > abs(position_qty) (10)` guard is false, so nothing fires.
        broken = _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w2")
        payload = json.loads(broken[0])
        payload["fill_price"] = None
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            (json.dumps(payload), broken[1], broken[2]),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(rows)
        assert realized == {}, (
            "a contract with an unusable journal row must keep IB's realizedPNL, "
            f"not publish a fabricated figure: {realized}"
        )
        assert any("incomplete" in r.message.lower() or "incomplete" in r.getMessage().lower()
                   for r in caplog.records), "the refusal must be logged"

    def test_missing_contracts_field_refuses(self):
        broken = _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w2")
        payload = json.loads(broken[0])
        payload.pop("contracts")
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            (json.dumps(payload), broken[1], broken[2]),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {}

    def test_unrecognised_action_refuses(self):
        broken = _row("o2", "TRANSFER", 10, 3.00, 0.0, _C60, "2026-08-11", "w2")
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            broken,
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {}

    def test_a_broken_row_does_not_taint_a_different_contract(self):
        broken = _row("x1", "BUY_OPTION", 10, 3.00, 0.0, _C70, "2026-08-11", "w2")
        payload = json.loads(broken[0])
        payload["fill_price"] = None
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            (json.dumps(payload), broken[1], broken[2]),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]
        realized = realized_pnl_by_exec_id(rows)
        assert realized == {"c1": 4000.0}

    def test_overlay_keeps_ib_figure_when_the_journal_is_unusable(self):
        broken = _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w2")
        payload = json.loads(broken[0])
        payload["fill_price"] = None
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            (json.dumps(payload), broken[1], broken[2]),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]

        fills = [_fill("c1", "SLV", 60.0, "C", "SLD", 10, 1234.0)]
        overlay_journal_realized_pnl(fills, reader=_FakeDb(rows=rows))
        assert fills[0]["realizedPNL"] == 1234.0
        assert fills[0].get("realizedPNLSource") != "journal"


class TestContractIdentityCorruption:
    """R-274 / REL-094: a malformed OPT row must be UNUSABLE, not excluded.

    ``_unusable_fill_key`` reported "excluded by design" for every row whose
    ``_bucket_key`` came back ``None``. Three of those are genuinely by
    design (BAG envelope, stock leg, rehydrated ``CLOSED``); a row that
    NAMES an option but carries a malformed ``right`` / ``strike`` /
    ``expiry`` is not — it is a fill the contract's history is missing, and
    dropping it silently fabricates realized P&L exactly as R-198 described.
    """

    @staticmethod
    def _corrupted(field, value):
        """BUY 10 @ $1 + BUY 10 @ $3 (``field`` corrupted) + SELL 10 @ $5.

        True average basis is $2/unit -> $3,000. Replaying only the kept
        open prices the close against $1 -> $4,000, and neither
        quantity-only guard fires because the dropped row removed its
        quantity and its cost together.
        """
        broken = _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w2")
        payload = json.loads(broken[0])
        payload[field] = value
        return [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            (json.dumps(payload), broken[1], broken[2]),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]

    def test_empty_right_refuses_instead_of_fabricating(self, caplog):
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(self._corrupted("right", ""))
        assert realized == {}, (
            "an option row whose `right` does not normalise is a MISSING fill, "
            f"not a by-design exclusion; must keep IB's figure, got {realized}"
        )
        assert any("incomplete" in r.getMessage().lower() for r in caplog.records)

    def test_null_strike_refuses_instead_of_fabricating(self):
        assert realized_pnl_by_exec_id(self._corrupted("strike", None)) == {}

    def test_unparseable_expiry_refuses_instead_of_fabricating(self):
        assert realized_pnl_by_exec_id(self._corrupted("expiry", "JAN 2027")) == {}

    def test_missing_right_field_entirely_refuses(self):
        broken = _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w2")
        payload = json.loads(broken[0])
        payload.pop("right")
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            (json.dumps(payload), broken[1], broken[2]),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {}

    def test_corruption_on_another_ticker_does_not_taint_slv(self):
        broken = _row("x1", "BUY_OPTION", 10, 3.00, 0.0,
                      {"ticker": "GLD", "strike": 300.0, "right": "C", "expiry": "20261016"},
                      "2026-08-11", "w2")
        payload = json.loads(broken[0])
        payload["right"] = ""
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            (json.dumps(payload), broken[1], broken[2]),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 4000.0}

    def test_bag_envelope_is_still_excluded_by_design(self):
        bag = json.dumps({"ticker": "SLV", "action": "BUY_OPTION", "contracts": 10,
                          "fill_price": 2.0, "commission": 0.0, "total_cost": 2000.0,
                          "ib_exec_id": "bag1", "right": "?", "date": "2026-08-07"})
        assert _unusable_fill_key((bag, "2026-08-07", "w1")) is None

    def test_stock_leg_is_still_excluded_by_design(self):
        stk = json.dumps({"ticker": "AAPL", "action": "BUY", "shares": 100,
                          "fill_price": 210.0, "commission": 0.0,
                          "ib_exec_id": "stk1", "date": "2026-08-07"})
        assert _unusable_fill_key((stk, "2026-08-07", "w1")) is None

    def test_rehydrated_closed_row_is_still_excluded_by_design(self):
        closed = _row("cl1", "CLOSED", 10, 3.00, 0.0, _C60, "2026-08-11", "w2")
        assert _unusable_fill_key(closed) is None

    # --- R-320 / REL-109: corruption that NORMALISES must poison too -------
    #
    # REL-094 keyed usability on `_bucket_key` returning None, so a contract
    # field that is malformed but still normalises forms its own phantom
    # bucket and never reaches the shape test. The healthy triple's true
    # realized is $3,000; each case below fabricated $4,000 with no warning.

    def test_zero_numeric_strike_refuses_instead_of_fabricating(self, caplog):
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(self._corrupted("strike", 0))
        assert realized == {}, (
            "strike 0 normalises to '0.0' and forms a phantom bucket, so the "
            f"healthy bucket prices its close against half a basis; got {realized}"
        )
        assert any("incomplete" in r.getMessage().lower() for r in caplog.records)

    def test_zero_string_strike_refuses_instead_of_fabricating(self):
        assert realized_pnl_by_exec_id(self._corrupted("strike", "0")) == {}

    def test_negative_strike_refuses_instead_of_fabricating(self):
        assert realized_pnl_by_exec_id(self._corrupted("strike", -60.0)) == {}

    def test_impossible_calendar_expiry_refuses_instead_of_fabricating(self):
        """Eight digits pass `_normalize_expiry` without being a real date."""
        assert realized_pnl_by_exec_id(self._corrupted("expiry", "20261340")) == {}

    def test_all_three_contract_fields_falsy_is_not_read_as_a_stock_leg(self):
        """`_is_option_shaped` tested truthiness, so 0/'' read as field-absent.

        A row that NAMES right, strike and expiry is asserting it is an
        option fill even when all three are falsy — it must poison, not be
        waved through as the by-design stock exclusion.
        """
        broken = _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w2")
        payload = json.loads(broken[0])
        payload["right"] = ""
        payload["strike"] = 0
        payload["expiry"] = ""
        row = (json.dumps(payload), broken[1], broken[2])
        assert _unusable_fill_key(row) == "SLV|*", (
            "all three option fields present-but-falsy must be read as a "
            "corrupt OPTION row, not as a stock leg naming none of them"
        )

    def test_zero_strike_is_field_presence_not_truthiness(self):
        """A numeric 0 strike is PRESENT; only a missing key is absent."""
        payload = {"ticker": "SLV", "action": "BUY_OPTION", "contracts": 10,
                   "fill_price": 3.0, "commission": 0.0, "ib_exec_id": "z1",
                   "date": "2026-08-11", "strike": 0}
        assert _unusable_fill_key((json.dumps(payload), "2026-08-11", "w1")) == "SLV|*"

    def test_plausible_contract_corruption_still_poisons_the_whole_ticker(self):
        """Poison scope must be TICKER|*, not the phantom bucket's own key."""
        assert _unusable_fill_key(
            self._corrupted("strike", 0)[1]
        ) == "SLV|*"

    def test_healthy_triple_is_untouched_by_the_plausibility_domain(self):
        """The plausibility check must not poison a legitimate replay."""
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row("o2", "BUY_OPTION", 10, 3.00, 0.0, _C60, "2026-08-11", "w2"),
            _row("c1", "SELL_OPTION", 10, 5.00, 0.0, _C60, "2026-08-20", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 3000.0}

    def test_bag_and_stock_rows_do_not_block_a_healthy_replay(self):
        """The pre-existing by-design exclusions keep their exact behaviour."""
        rows = [
            (json.dumps({"ticker": "SLV", "action": "BUY_OPTION", "contracts": 10,
                         "fill_price": 2.0, "commission": 0.0, "total_cost": 2000.0,
                         "ib_exec_id": "bag1", "right": "?", "date": "2026-08-07"}),
             "2026-08-07", "w1"),
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w2"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {"c1": 2000.0}


class TestBoundedJournalRead:
    """R-203: this read sits inside the orders-sync money path.

    The unpaginated, unbounded ``SELECT ... WHERE ticker IN (...)`` over the
    sync ``libsql_experimental`` connection exposes no execute timeout and
    holds the GIL while blocked, so a stall wedges orders-sync with no
    heartbeat and no executed-order rows landing. The sibling
    ``journal_basis._fetch_journal_rows_for_tickers`` was converted to a
    200-row keyset pager for exactly this reason.
    """

    def _paged_row(self, trade_id, *args, **kwargs):
        payload, filled_at, written_at = _row(*args, **kwargs)
        return (trade_id, payload, filled_at, written_at)

    def test_read_is_paged_at_two_hundred_rows_per_statement(self):
        statements = []
        # 450 rows for one contract: 200 + 200 + 50.
        rows = [
            self._paged_row(
                f"t{i:04d}", f"o{i}", "BUY_OPTION", 1, 1.00, 0.0, _C60, "2026-08-07", f"w{i}"
            )
            for i in range(450)
        ]

        class _Pager:
            def execute(_self, sql, args=()):
                statements.append((sql, tuple(args)))
                cursor = args[0]
                limit = args[-1]
                page = [row for row in rows if row[0] > cursor][: int(limit)]
                return _FakeCursor(page)

        fills = [_fill("o0", "SLV", 60.0, "C", "BOT", 1, None)]
        journal_realized_pnl_for_fills(_Pager(), fills)

        assert len(statements) == 3, f"expected keyset pagination, got {len(statements)}"
        for sql, args in statements:
            assert "LIMIT" in sql.upper(), sql
            assert args[-1] == 200, args

    def test_overlay_does_not_read_the_journal_over_the_unbounded_connection(
        self, monkeypatch
    ):
        """The default overlay transport must be the bounded one.

        ``db.client.get_db()`` is the sync libsql connection whose own module
        docstring states it exposes no connect or execute timeout.
        """
        import db.client as db_client

        def _refuse():
            raise AssertionError(
                "journal_realized must not read the journal over get_db()"
            )

        monkeypatch.setattr(db_client, "get_db", _refuse)
        calls = []

        def _fake_hrana_query(sql, args=(), timeout=None):
            calls.append((sql, tuple(args)))
            return []

        import db.hrana_http as hrana_http

        monkeypatch.setattr(hrana_http, "hrana_query", _fake_hrana_query)

        fills = [_fill("c1", "SLV", 60.0, "C", "SLD", 10, 1234.5)]
        overlay_journal_realized_pnl(fills)
        assert fills[0]["realizedPNL"] == 1234.5
        assert calls, "the overlay must have gone through the bounded transport"
        assert "journal" in calls[0][0].lower()


class TestCrossWriterSameDayPartials:
    """T-184: two genuinely distinct same-day partials, one per writer.

    ``contract_fill_fingerprint`` is (contract, ET session date, signed qty) —
    by construction the ONLY identity the two writers agree on, because
    ``ib_exec_id`` is a numeric Flex ``tradeID`` on one side and a dotted IB
    API execId on the other. So a cross-writer collision is genuinely
    ambiguous: it is either the same fill written twice (the common case the
    dedupe exists for, T-124) or two real equal-size partials.

    Nothing in the journal can separate them. ``execution_time`` cannot:
    ``monitor_daemon/handlers/journal_sync.py`` writes it and
    ``journal_rehydrate._bucket_to_entry`` does not, so a cross-writer pair
    NEVER carries two comparable times in production — keying on it would
    treat every dual-written fill as distinct and re-introduce exactly the
    double-count T-124 fixed. Neither can price: the ambiguous case is equal
    size at equal price, and narrowing the fingerprint only ever counts MORE
    rows.

    So the drop is deliberate, and it stays: ``journal_basis
    ._claim_exec_parts`` states the module's standing preference — "under-
    counting is recoverable ... while double-counting silently inflates the
    basis". What must not stay silent is what the suppression costs LATER on
    the same contract.
    """

    # The daemon's dotted IB API execId and the Flex numeric tradeID for two
    # real 10-lot partials of the same 20-lot SLV position on 2026-08-24.
    API_ID = "0000e0d5.68abc123.01.01"
    FLEX_ID = "9998092102"

    def _twenty_lot_open_then_two_partials(self, *, flex_has_time: bool):
        """BUY 20 @ $1.00, then SELL 10 @ $3.00 twice on 2026-08-24.

        Basis is $100/contract, so each real partial realizes
        10 x ($300 - $100) = $2,000, i.e. $4,000 for the session.
        """
        return [
            _row("o1", "BUY_OPTION", 20, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row(self.API_ID, "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w2",
                 "2026-08-24T15:30:00-04:00"),
            _row(self.FLEX_ID, "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3",
                 "2026-08-24T15:45:00-04:00" if flex_has_time else None),
        ]

    def test_second_partial_is_dropped_and_only_one_figure_is_attributed(self):
        """The AC case, pinned as the deliberate under-count.

        Distinct ``execution_time``s do NOT make the pair separable — the
        fingerprint does not carry time, and a real Flex row has none at all.
        Money: $4,000 of realized P&L, $2,000 of it journal-attributed.
        """
        realized = realized_pnl_by_exec_id(
            self._twenty_lot_open_then_two_partials(flex_has_time=True)
        )
        assert realized == {self.API_ID: 2000.0}, (
            "the cross-writer collision must under-count, never double-count: "
            f"{realized}"
        )

    def test_the_dropped_partial_falls_back_to_ib_and_is_not_halved(self):
        """The suppressed fill keeps IB's own figure, it is not zeroed.

        Both real partials reach ``/orders`` under dotted API execIds. Only
        one of them is in the journal under an api id, so the other is served
        from IB and labelled ``ib``. The blotter therefore shows
        $2,000 (journal) + $1,300 (IB, avgCost-drifted) = $3,300 against a
        true $4,000 — understated by $700, and mixed-source.
        """
        fills = [
            _fill(self.API_ID, "SLV", 60.0, "C", "SLD", 10, 1300.0),
            _fill("0000e0d5.68abc123.02.01", "SLV", 60.0, "C", "SLD", 10, 1300.0),
        ]
        overlay_journal_realized_pnl(
            fills,
            reader=_FakeDb(rows=self._twenty_lot_open_then_two_partials(flex_has_time=True)),
        )
        assert fills[0]["realizedPNL"] == 2000.0
        assert fills[0]["realizedPNLSource"] == "journal"
        assert fills[1]["realizedPNL"] == 1300.0, "the dropped partial must keep IB's figure"
        assert fills[1]["realizedPNLSource"] == "ib"

    def test_a_real_flex_row_has_no_execution_time_so_it_wins_the_attribution(self):
        """Production shape, and the reason the correction never lands.

        ``_ordered`` sorts a day by ``execution_time``; the Flex row has none,
        so "" sorts it FIRST and the daemon's row is the one suppressed. The
        surviving key is then a Flex ``tradeID``, which no IB fill carries, so
        ``apply_journal_realized_pnl`` matches nothing and BOTH partials ship
        IB's figure. Pinned as-is, not endorsed.
        """
        realized = realized_pnl_by_exec_id(
            self._twenty_lot_open_then_two_partials(flex_has_time=False)
        )
        assert realized == {self.FLEX_ID: 2000.0}

        fills = [_fill(self.API_ID, "SLV", 60.0, "C", "SLD", 10, 1300.0)]
        apply_journal_realized_pnl(fills, realized)
        assert fills[0]["realizedPNL"] == 1300.0
        assert fills[0]["realizedPNLSource"] == "ib"

    def test_a_close_after_the_phantom_inventory_is_reopened_is_withheld(self, caplog):
        """The suppression's real damage: it under-closes the position.

        BUY 20 @ $1.00, both 10-lot partials sold at $3.00 (one suppressed),
        then a re-entry BUY 10 @ $5.00 and a SELL 10 @ $6.00.

        Truth: the position was flat before the re-entry, so the re-entry
        basis is $500/contract and the last close realizes
        10 x ($600 - $500) = $1,000.
        Untreated replay: 10 phantom contracts at $100 blend with the
        re-entry into $300/contract, and the last close reports
        10 x ($600 - $300) = $3,000 — $2,000 fabricated, with neither
        quantity-only guard firing.
        """
        rows = self._twenty_lot_open_then_two_partials(flex_has_time=True) + [
            _row("o2", "BUY_OPTION", 10, 5.00, 0.0, _C60, "2026-08-25", "w4"),
            _row("c3", "SELL_OPTION", 10, 6.00, 0.0, _C60, "2026-08-26", "w5"),
        ]
        with caplog.at_level("WARNING"):
            realized = realized_pnl_by_exec_id(rows)
        assert "c3" not in realized, (
            "a close priced against phantom inventory must keep IB's figure, "
            f"not publish $3,000 against a true $1,000: {realized}"
        )
        assert realized == {self.API_ID: 2000.0}
        assert any("incomplete" in record.getMessage().lower() for record in caplog.records)

    def test_a_suppressed_opening_partial_withholds_every_later_figure(self):
        """A suppressed OPEN removes its cost, not just its quantity.

        BUY 10 @ $1.00 (api) and BUY 10 @ $2.00 (Flex) on the same day are one
        fingerprint, so the second is suppressed. True basis is $150/contract,
        so SELL 10 @ $3.00 realizes 10 x ($300 - $150) = $1,500; the replay
        prices it against $100 and reports $2,000 — $500 fabricated.
        """
        rows = [
            _row("o1", "BUY_OPTION", 10, 1.00, 0.0, _C60, "2026-08-07", "w1",
                 "2026-08-07T14:00:00-04:00"),
            _row(self.FLEX_ID, "BUY_OPTION", 10, 2.00, 0.0, _C60, "2026-08-07", "w2",
                 "2026-08-07T15:00:00-04:00"),
            _row("c1", "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3"),
        ]
        assert realized_pnl_by_exec_id(rows) == {}

    def test_a_dual_written_close_still_yields_the_later_scale_out(self):
        """The T-124 correction must survive: no re-entry, no taint.

        The whole point of the module is the SLV shape — a multi-day scale-out
        whose IB figure has drifted. A cross-writer duplicate on the first
        partial leaves the per-unit basis exactly right, so the later partial
        keeps its journal figure and only a re-open would forfeit it.
        """
        rows = [
            _row("o1", "BUY_OPTION", 30, 1.00, 0.0, _C60, "2026-08-07", "w1"),
            _row(self.API_ID, "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w2"),
            _row(self.FLEX_ID, "SELL_OPTION", 10, 3.00, 0.0, _C60, "2026-08-24", "w3"),
            _row("c2", "SELL_OPTION", 10, 4.00, 0.0, _C60, "2026-08-26", "w4"),
        ]
        assert realized_pnl_by_exec_id(rows) == {self.API_ID: 2000.0, "c2": 3000.0}
