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
