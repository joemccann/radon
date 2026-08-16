#!/usr/bin/env python3
"""TDD tests for ExpirySweepHandler — post-expiry journal sweep.

Incident 2026-08-09 (SPCX 25x $150C expired worthless Fri 2026-08-07):
expiration emits no execution, so no fill-driven path ever closed the
journal opener — the position stayed "open" forever and downstream
consumers (position reconcile, blotter isOpen stats) paged on it.

Design contract:
  - BaseHandler subclass, service_name="journal-expiry-sweep"
  - requires_market_hours=False, daily interval — pure Turso, no IB
  - Candidate = open option position (net qty != 0 per contract key)
    whose expiry is >= SETTLE_WINDOW_DAYS calendar days in the past (ET)
  - Close row flattens the open qty at $0.00 with ib_codes "Ep" and a
    deterministic synthetic exec id (idempotent re-runs)
  - Action follows journal_sync close labels: SELL_OPTION closes a long,
    BUY_TO_CLOSE covers a short
  - Guards: any journal OR executed_orders activity for the contract
    dated after the expiry (assignment/exercise trace) skips the
    candidate; stock rows never touched; per-cycle write cap
  - Heartbeat ok EVERY cycle with {candidates, closed, skipped_guarded}
  - Raises on DB unavailable so last_run never latches on a soft failure
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import monitor_daemon.handlers.expiry_sweep as expiry_sweep  # noqa: E402
from monitor_daemon.handlers.expiry_sweep import (  # noqa: E402
    ExpirySweepHandler,
    MAX_CLOSES_PER_CYCLE,
    SETTLE_WINDOW_DAYS,
    synthetic_exec_id,
)

# Window-relative dates (repo convention — hardcoded dates rot in CI).
# Derived from the ET calendar date — the handler's clock — so early-UTC
# CI runs don't flake across the local/ET date boundary.
TODAY_ET = datetime.now(ZoneInfo("America/New_York")).date()
EXPIRED = TODAY_ET - timedelta(days=2)
EXPIRED_COMPACT = EXPIRED.strftime("%Y%m%d")
EXPIRED_ISO = EXPIRED.strftime("%Y-%m-%d")
IN_SETTLE_WINDOW_COMPACT = (TODAY_ET - timedelta(days=1)).strftime("%Y%m%d")
OPEN_DATE_ISO = (TODAY_ET - timedelta(days=30)).strftime("%Y-%m-%d")
POST_EXPIRY_ISO = (EXPIRED + timedelta(days=1)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _journal_row(
    action: str,
    contracts: int,
    *,
    ticker: str = "SPCX",
    strike: float = 150.0,
    right: str = "C",
    expiry: str = EXPIRED_COMPACT,
    date_str: str = OPEN_DATE_ISO,
    ib_exec_id: str = "",
    shares: int | None = None,
) -> tuple:
    payload: dict = {
        "ticker": ticker,
        "action": action,
        "date": date_str,
    }
    if shares is not None:
        payload["shares"] = shares
    else:
        payload["contracts"] = contracts
        payload["strike"] = strike
        payload["right"] = right
        payload["expiry"] = expiry
    if ib_exec_id:
        payload["ib_exec_id"] = ib_exec_id
    return (json.dumps(payload), date_str, date_str)


def _executed_row(
    *,
    ticker: str = "SPCX",
    strike: float = 150.0,
    right: str = "C",
    expiry: str = EXPIRED_COMPACT,
    fill_time: str = f"{POST_EXPIRY_ISO}T14:00:00Z",
    exec_id: str = "0001abcd.0001.01",
) -> tuple:
    payload = {
        "execId": exec_id,
        "symbol": ticker,
        "contract": {
            "secType": "OPT",
            "symbol": ticker,
            "strike": strike,
            "right": right,
            "expiry": expiry,
        },
    }
    return (exec_id, None, json.dumps(payload), fill_time, fill_time)


def _make_db(journal_rows: list, executed_rows: list | None = None) -> MagicMock:
    db = MagicMock()

    def _execute(sql: str, params: tuple = ()):
        cursor = MagicMock(spec=["fetchall"])
        if "executed_orders" in sql:
            cursor.fetchall.return_value = executed_rows or []
        else:
            cursor.fetchall.return_value = journal_rows
        return cursor

    db.execute.side_effect = _execute
    return db


def _make_handler(db) -> ExpirySweepHandler:
    handler = ExpirySweepHandler()
    handler._open_db = lambda: db
    handler.record_cycle_health = MagicMock()
    return handler


@pytest.fixture
def upserts(monkeypatch):
    mock = MagicMock(name="upsert_journal_entry")
    monkeypatch.setattr(expiry_sweep, "upsert_journal_entry", mock)
    return mock


# ---------------------------------------------------------------------------
# Identity / wiring
# ---------------------------------------------------------------------------

class TestIdentity:
    def test_service_name(self):
        assert ExpirySweepHandler.service_name == "journal-expiry-sweep"

    def test_daily_pure_turso(self):
        handler = ExpirySweepHandler()
        assert handler.requires_market_hours is False
        assert handler.interval_seconds == 86_400

    def test_synthetic_exec_id_is_deterministic(self):
        first = synthetic_exec_id("SPCX", EXPIRED_COMPACT, 150.0, "C")
        assert first == f"ep-SPCX-{EXPIRED_COMPACT}-150C"
        assert first == synthetic_exec_id("SPCX", EXPIRED_COMPACT, 150.0, "C")

    def test_registered_in_create_daemon(self, tmp_path: Path):
        with patch("monitor_daemon.run.STATE_FILE", tmp_path / "daemon_state.json"):
            from monitor_daemon.run import create_daemon

            daemon = create_daemon()
        assert "expiry_sweep" in {h.name for h in daemon.handlers}


# ---------------------------------------------------------------------------
# Closing expired positions
# ---------------------------------------------------------------------------

class TestClosesExpiredPositions:
    def test_closes_stale_expired_long(self, upserts):
        db = _make_db([_journal_row("BUY_OPTION", 25)])
        handler = _make_handler(db)

        result = handler.execute()

        assert result["candidates"] == 1
        assert result["closed"] == 1
        assert result["skipped_guarded"] == 0
        trade_id, payload = upserts.call_args[0][0], upserts.call_args[0][1]
        assert trade_id == f"ep-SPCX-{EXPIRED_COMPACT}-150C"
        assert payload["action"] == "SELL_OPTION"
        assert payload["contracts"] == 25
        assert payload["fill_price"] == 0.0
        assert payload["total_cost"] == 0.0
        assert payload["ib_codes"] == "Ep"
        assert payload["ib_exec_id"] == trade_id
        assert payload["strike"] == 150.0
        assert payload["right"] == "C"
        assert payload["expiry"] == EXPIRED_COMPACT
        assert payload["date"] == EXPIRED_ISO
        assert upserts.call_args.kwargs.get("filled_at") == EXPIRED_ISO

    def test_closes_expired_short_with_buy_to_close(self, upserts):
        db = _make_db(
            [_journal_row("SELL_TO_OPEN", 10, ticker="XYZ", strike=50.0, right="P")]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 1
        payload = upserts.call_args[0][1]
        assert payload["action"] == "BUY_TO_CLOSE"
        assert payload["contracts"] == 10
        assert payload["fill_price"] == 0.0
        assert payload["ib_codes"] == "Ep"

    def test_partial_close_flattens_residual_qty(self, upserts):
        db = _make_db(
            [
                _journal_row("BUY_OPTION", 25),
                _journal_row("SELL_OPTION", 10, date_str=OPEN_DATE_ISO),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 1
        assert upserts.call_args[0][1]["contracts"] == 15


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

class TestGuards:
    def test_post_expiry_journal_activity_skips(self, upserts):
        """Assignment/exercise trace after expiry — never overwrite it."""
        db = _make_db(
            [
                _journal_row("BUY_OPTION", 25),
                _journal_row("SELL_OPTION", 5, date_str=POST_EXPIRY_ISO),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 0
        assert result["skipped_guarded"] == 1
        upserts.assert_not_called()

    def test_post_expiry_executed_orders_activity_skips(self, upserts):
        db = _make_db([_journal_row("BUY_OPTION", 25)], [_executed_row()])
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 0
        assert result["skipped_guarded"] == 1
        upserts.assert_not_called()

    def test_within_settle_window_not_a_candidate(self, upserts):
        db = _make_db([_journal_row("BUY_OPTION", 25, expiry=IN_SETTLE_WINDOW_COMPACT)])
        handler = _make_handler(db)

        result = handler.execute()

        assert result["candidates"] == 0
        assert result["closed"] == 0
        upserts.assert_not_called()

    def test_stock_rows_never_touched(self, upserts):
        db = _make_db([_journal_row("BUY", 0, shares=100)])
        handler = _make_handler(db)

        result = handler.execute()

        assert result["candidates"] == 0
        upserts.assert_not_called()

    def test_unparseable_expiry_skips_guarded(self, upserts):
        db = _make_db([_journal_row("BUY_OPTION", 25, expiry="garbage8")])
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 0
        assert result["skipped_guarded"] == 1
        upserts.assert_not_called()

    def test_per_cycle_write_cap(self, upserts):
        rows = [
            _journal_row("BUY_OPTION", 1, ticker=f"T{i:03d}")
            for i in range(MAX_CLOSES_PER_CYCLE + 5)
        ]
        db = _make_db(rows)
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == MAX_CLOSES_PER_CYCLE
        assert result["cap_hit"] is True
        assert upserts.call_count == MAX_CLOSES_PER_CYCLE


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_second_run_does_not_double_close(self, upserts):
        """After the sweep's own close row lands, net qty is 0 — re-run is a no-op."""
        close_id = synthetic_exec_id("SPCX", EXPIRED_COMPACT, 150.0, "C")
        db = _make_db(
            [
                _journal_row("BUY_OPTION", 25),
                _journal_row(
                    "SELL_OPTION", 25, date_str=EXPIRED_ISO, ib_exec_id=close_id
                ),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["candidates"] == 0
        assert result["closed"] == 0
        upserts.assert_not_called()

    def test_existing_synthetic_id_guards_even_with_nonzero_net(self, upserts):
        """Belt-and-suspenders: a present ep- row always blocks a second write."""
        close_id = synthetic_exec_id("SPCX", EXPIRED_COMPACT, 150.0, "C")
        db = _make_db(
            [
                _journal_row("BUY_OPTION", 25),
                _journal_row(
                    "SELL_OPTION", 10, date_str=EXPIRED_ISO, ib_exec_id=close_id
                ),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 0
        assert result["skipped_guarded"] == 1
        upserts.assert_not_called()


# ---------------------------------------------------------------------------
# Heartbeat / failure semantics
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_ok_heartbeat_on_zero_candidate_cycle(self, upserts):
        handler = _make_handler(_make_db([]))

        result = handler.execute()

        assert result == {"candidates": 0, "closed": 0, "skipped_guarded": 0}
        state = handler.record_cycle_health.call_args[0][0]
        assert state == "ok"
        detail = handler.record_cycle_health.call_args.kwargs["error"]
        assert detail["candidates"] == 0
        assert detail["closed"] == 0
        assert detail["skipped_guarded"] == 0

    def test_ok_heartbeat_carries_counts_after_close(self, upserts):
        handler = _make_handler(_make_db([_journal_row("BUY_OPTION", 25)]))

        handler.execute()

        state = handler.record_cycle_health.call_args[0][0]
        assert state == "ok"
        detail = handler.record_cycle_health.call_args.kwargs["error"]
        assert detail == {"candidates": 1, "closed": 1, "skipped_guarded": 0}

    def test_db_unavailable_raises(self):
        handler = ExpirySweepHandler()
        handler._open_db = lambda: None
        with pytest.raises(RuntimeError):
            handler.execute()

    def test_write_failure_raises_for_retry(self, upserts):
        """A failed upsert is THIS sweep failing — raise so last_run never
        latches (idempotent dedup makes the retry safe)."""
        upserts.side_effect = OSError("stream not found")
        handler = _make_handler(_make_db([_journal_row("BUY_OPTION", 25)]))
        with pytest.raises(Exception):
            handler.execute()


def _occ_symbol(root: str, expiry_compact: str, right: str, strike: float) -> str:
    """OCC option symbol as the Flex rehydrate writer stores it in ticker."""
    return f"{root:<6}{expiry_compact[2:]}{right}{int(round(strike * 1000)):08d}"


class TestCrossConventionDedup:
    """Production remediation 2026-08-09: the journal holds the SAME real
    contract under two ticker conventions (real-time daemon: plain root +
    per-exec rows; Flex rehydrate: OCC symbol + '+'-joined composite rows),
    plus 'CLOSED' round-trip rows the sign map didn't know. The first sweep
    cycle double-counted duplicated fills and closed a phantom short created
    by a mislabeled SELL_TO_OPEN. These shapes are lifted verbatim from the
    live Turso journal."""

    def test_duplicate_fills_across_conventions_counted_once(self, upserts):
        """SPCX shape: plain exec rows 8+8+9 and an OCC composite 25 are the
        same fills — one close of 25, never 50."""
        occ = _occ_symbol("SPCX", EXPIRED_COMPACT, "C", 150.0)
        db = _make_db(
            [
                _journal_row("BUY_OPTION", 8, ib_exec_id="00018c6a.6a71e667.01.01"),
                _journal_row("BUY_OPTION", 8, ib_exec_id="00018c6a.6a71e669.01.01"),
                _journal_row("BUY_OPTION", 9, ib_exec_id="00018c6a.6a71e66a.01.01"),
                _journal_row(
                    "BUY_OPTION", 25, ticker=occ, ib_exec_id="9998981349+9998981410"
                ),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 1
        assert upserts.call_count == 1
        trade_id, payload = upserts.call_args[0][0], upserts.call_args[0][1]
        assert trade_id == f"ep-SPCX-{EXPIRED_COMPACT}-150C"
        assert payload["ticker"] == "SPCX"
        assert payload["contracts"] == 25
        assert payload["action"] == "SELL_OPTION"

    def test_phantom_short_from_mislabeled_close_not_swept(self, upserts):
        """AAOI shape: OCC round trip (BUY 100 then SELL 100) plus the same
        sale re-journaled as plain SELL_TO_OPEN 100. Real position is flat —
        closing the phantom short fabricates a windfall gain."""
        occ = _occ_symbol("AAOI", EXPIRED_COMPACT, "C", 105.0)
        sale_date = (EXPIRED - timedelta(days=7)).strftime("%Y-%m-%d")
        buy_date = (EXPIRED - timedelta(days=40)).strftime("%Y-%m-%d")
        db = _make_db(
            [
                _journal_row(
                    "BUY_OPTION", 100, ticker=occ, strike=105.0,
                    date_str=buy_date, ib_exec_id="9235885205+9235885212",
                ),
                _journal_row(
                    "SELL_OPTION", 100, ticker=occ, strike=105.0,
                    date_str=sale_date, ib_exec_id="9467415501+9467415506",
                ),
                _journal_row(
                    "SELL_TO_OPEN", 100, ticker="AAOI", strike=105.0,
                    date_str=sale_date, ib_exec_id="0002920b.69fe5a54.01.01",
                ),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 0
        upserts.assert_not_called()

    def test_closed_round_trip_rows_net_zero(self, upserts):
        """OCC 'CLOSED' rows are completed round trips and contribute nothing
        to their own contract's net; an independent plain short on a DIFFERENT
        contract still sweeps.

        REL-025: this case used to put the round trip on the SAME contract as
        the plain short and expect a sweep. A CLOSED row carries no close date,
        so from the journal alone that shape is indistinguishable from the
        daemon having re-journaled the round trip's own sale — and guessing
        wrong writes a $0.00 BUY_TO_CLOSE for a contract never held. The
        same-contract shape is now pinned as no-sweep by
        TestRoundTripRowsAbsorbTheDuplicateShort; this keeps the original
        intent (a round trip does not disarm unrelated sweeps).
        """
        occ = _occ_symbol("TSLA", EXPIRED_COMPACT, "P", 380.0)
        db = _make_db(
            [
                _journal_row(
                    "CLOSED", 10, ticker=occ, strike=380.0, right="P",
                    date_str=(EXPIRED - timedelta(days=50)).strftime("%Y-%m-%d"),
                    ib_exec_id="9217417513+9399322577",
                ),
                _journal_row(
                    "SELL_TO_OPEN", 10, ticker="TSLA", strike=400.0, right="P",
                    date_str=(EXPIRED - timedelta(days=7)).strftime("%Y-%m-%d"),
                    ib_exec_id="0001eb2e.69fdf46c.01.01",
                ),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 1
        payload = upserts.call_args[0][1]
        assert payload["action"] == "BUY_TO_CLOSE"
        assert payload["contracts"] == 10
        assert payload["strike"] == 400.0
        assert payload["ticker"] == "TSLA"

    def test_occ_only_open_closes_under_normalized_ticker(self, upserts):
        """ETHA shape: opener exists only under the OCC symbol — the close
        row and synthetic id use the normalized root."""
        occ = _occ_symbol("ETHA", EXPIRED_COMPACT, "C", 30.0)
        db = _make_db(
            [
                _journal_row(
                    "BUY_OPTION", 200, ticker=occ, strike=30.0,
                    ib_exec_id="9076523139+9077019421",
                )
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 1
        trade_id, payload = upserts.call_args[0][0], upserts.call_args[0][1]
        assert trade_id == f"ep-ETHA-{EXPIRED_COMPACT}-30C"
        assert payload["ticker"] == "ETHA"
        assert payload["contracts"] == 200

    def test_unequal_cross_convention_overlap_is_guarded(self, upserts):
        """Composite 25 vs plain 20 on the same day/side — cannot tell which
        fills duplicate. Never guess, never write."""
        occ = _occ_symbol("SPCX", EXPIRED_COMPACT, "C", 150.0)
        db = _make_db(
            [
                _journal_row("BUY_OPTION", 20, ib_exec_id="00018c6a.6a71e667.01.01"),
                _journal_row(
                    "BUY_OPTION", 25, ticker=occ, ib_exec_id="9998981349+9998981410"
                ),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 0
        assert result["skipped_guarded"] == 1
        upserts.assert_not_called()

    def test_cross_date_mixed_convention_history_is_guarded(self, upserts):
        """ETHA 15C shape: composite -200 and plain -200 on DIFFERENT dates
        never pair in the per-day dedup, so the net doubles. Unpaired
        contributions from both conventions = unattributable — skip."""
        occ = _occ_symbol("ETHA", EXPIRED_COMPACT, "C", 15.0)
        db = _make_db(
            [
                _journal_row(
                    "SELL_TO_OPEN", 200, ticker="ETHA", strike=15.0,
                    date_str=(EXPIRED - timedelta(days=20)).strftime("%Y-%m-%d"),
                    ib_exec_id="0001aaaa.11111111.01.01",
                ),
                _journal_row(
                    "SELL_OPTION", 200, ticker=occ, strike=15.0,
                    date_str=(EXPIRED - timedelta(days=18)).strftime("%Y-%m-%d"),
                    ib_exec_id="9076523139+9077019421",
                ),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 0
        assert result["skipped_guarded"] == 1
        upserts.assert_not_called()

    def test_numeric_flex_id_counts_as_rehydrate_row(self, upserts):
        """Exact ETHA 15C production shape: the rehydrate twin carries a
        single NUMERIC Flex trade id (no '+') under the OCC ticker, dated one
        day off from the daemon's hex-exec-id row. Same real fill, never
        pairs — must be ambiguous, not net -400."""
        occ = _occ_symbol("ETHA", EXPIRED_COMPACT, "C", 15.0)
        db = _make_db(
            [
                _journal_row(
                    "SELL_TO_OPEN", 200, ticker="ETHA", strike=15.0,
                    date_str=(EXPIRED - timedelta(days=19)).strftime("%Y-%m-%d"),
                    ib_exec_id="00012871.6a34cc91.01.01",
                ),
                _journal_row(
                    "SELL_TO_OPEN", 200, ticker=occ, strike=15.0,
                    date_str=(EXPIRED - timedelta(days=20)).strftime("%Y-%m-%d"),
                    ib_exec_id="9741721501",
                ),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 0
        assert result["skipped_guarded"] == 1
        upserts.assert_not_called()

    def test_unknown_action_guards_contract(self, upserts):
        db = _make_db(
            [
                _journal_row("BUY_OPTION", 25),
                _journal_row("MYSTERY_ACTION", 5, date_str=OPEN_DATE_ISO),
            ]
        )
        handler = _make_handler(db)

        result = handler.execute()

        assert result["closed"] == 0
        assert result["skipped_guarded"] == 1
        upserts.assert_not_called()


class TestSqlSchemaPins:
    """The MagicMock db here cannot catch schema drift — production incident
    2026-08-09: the executed_orders SELECT named a nonexistent order_ref
    column and every sweep cycle errored with SQL_INPUT_ERROR. Pin the
    handler's SELECT lists against the real migration DDL."""

    @staticmethod
    def _table_columns(table: str) -> set:
        import re

        for path in sorted((SCRIPTS_DIR / "db" / "migrations").glob("*.sql")):
            match = re.search(
                rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\);",
                path.read_text(),
                re.S,
            )
            if match:
                columns = set()
                for line in match.group(1).splitlines():
                    word = line.strip().rstrip(",").split()
                    if word and word[0].isidentifier():
                        columns.add(word[0])
                return columns
        raise AssertionError(f"no CREATE TABLE for {table} in migrations")

    @pytest.mark.parametrize("table", ["journal", "executed_orders"])
    def test_selects_only_real_columns(self, table):
        import inspect
        import re

        source = inspect.getsource(expiry_sweep)
        selects = re.findall(
            rf"SELECT\s+((?:(?!\bSELECT\b|\bFROM\b).)*?)\s+FROM\s+{table}\b",
            source,
            re.S | re.I,
        )
        assert selects, f"no SELECT against {table} found in expiry_sweep"
        real = self._table_columns(table)
        for select in selects:
            selected = {c.strip() for c in select.split(",")}
            assert selected <= real, (
                f"expiry_sweep selects {sorted(selected - real)} which do not "
                f"exist on {table} (real columns: {sorted(real)})"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ---------------------------------------------------------------------------
# REL-025 (R-063, R-074) — the sweep drops CLOSED rows BEFORE the comp/plain
# pairing, and that pairing is the only defence against the same fills being
# journaled under both id conventions. A Flex-rehydrated completed round trip
# is a single CLOSED row, so it contributed nothing and could not pair against
# the real-time daemon's duplicate SELL_TO_OPEN for the same sale; unpaired_comp
# was never set so ambiguous stayed False. Net then read as a genuine short and
# the sweep upserted a BUY_TO_CLOSE at fill_price 0.0 for a contract never
# held — a fabricated windfall in realized P&L plus a phantom position event.
#
# R-074: both expiry guards used strict greater-than, so an assignment,
# exercise or closing fill booked ON the expiration date did not guard.
# ---------------------------------------------------------------------------


class TestRoundTripRowsAbsorbTheDuplicateShort:
    def test_closed_round_trip_absorbs_the_daemons_duplicate_sale(self, upserts):
        """The R-063 drill. A CLOSED row carries no close date, so it cannot be
        date-paired — it must still absorb the otherwise-unpaired plain row for
        the same contract rather than let it read as an open short."""
        occ = _occ_symbol("TSLA", EXPIRED_COMPACT, "P", 400.0)
        db = _make_db(
            [
                _journal_row(
                    "CLOSED", 10, ticker=occ, strike=400.0, right="P",
                    date_str=(EXPIRED - timedelta(days=50)).strftime("%Y-%m-%d"),
                    ib_exec_id="9217417513+9399322577",
                ),
                _journal_row(
                    "SELL_TO_OPEN", 10, ticker="TSLA", strike=400.0, right="P",
                    date_str=(EXPIRED - timedelta(days=7)).strftime("%Y-%m-%d"),
                    ib_exec_id="0001eb2e.69fdf46c.01.01",
                ),
            ]
        )

        result = _make_handler(db).execute()

        assert result["closed"] == 0
        upserts.assert_not_called()

    def test_a_genuine_short_with_no_round_trip_still_sweeps(self, upserts):
        """Control: the absorption must not disarm the sweep entirely."""
        db = _make_db(
            [
                _journal_row(
                    "SELL_TO_OPEN", 10, ticker="TSLA", strike=400.0, right="P",
                    date_str=(EXPIRED - timedelta(days=7)).strftime("%Y-%m-%d"),
                    ib_exec_id="0001eb2e.69fdf46c.01.01",
                ),
            ]
        )

        result = _make_handler(db).execute()

        assert result["closed"] == 1
        assert upserts.call_args[0][1]["action"] == "BUY_TO_CLOSE"

    def test_a_round_trip_absorbs_only_its_own_quantity(self, upserts):
        """A 10-lot round trip cannot account for a 25-lot short: the residual
        15 is a real open position and must still be closed."""
        occ = _occ_symbol("TSLA", EXPIRED_COMPACT, "P", 400.0)
        db = _make_db(
            [
                _journal_row(
                    "CLOSED", 10, ticker=occ, strike=400.0, right="P",
                    date_str=(EXPIRED - timedelta(days=50)).strftime("%Y-%m-%d"),
                    ib_exec_id="9217417513+9399322577",
                ),
                _journal_row(
                    "SELL_TO_OPEN", 25, ticker="TSLA", strike=400.0, right="P",
                    date_str=(EXPIRED - timedelta(days=7)).strftime("%Y-%m-%d"),
                    ib_exec_id="0001eb2e.69fdf46c.01.01",
                ),
            ]
        )

        result = _make_handler(db).execute()

        assert result["closed"] == 1
        assert upserts.call_args[0][1]["contracts"] == 15


class TestExpiryDateGuardsAreInclusive:
    def test_a_fill_booked_on_the_expiry_date_guards_the_candidate(self, upserts):
        """Same-day settlement: a partial close booked exactly on the
        expiration date leaves a short net, but the contract was demonstrably
        handled on expiry day — a strict > skipped that guard entirely."""
        db = _make_db(
            [
                _journal_row(
                    "SELL_TO_OPEN", 25, ticker="TSLA", strike=400.0, right="P",
                    date_str=(EXPIRED - timedelta(days=7)).strftime("%Y-%m-%d"),
                    ib_exec_id="0001eb2e.69fdf46c.01.01",
                ),
                _journal_row(
                    "BUY_TO_CLOSE", 10, ticker="TSLA", strike=400.0, right="P",
                    date_str=EXPIRED.strftime("%Y-%m-%d"),
                    ib_exec_id="0001eb2e.69fdf46c.01.02",
                ),
            ]
        )

        result = _make_handler(db).execute()

        assert result["closed"] == 0
        upserts.assert_not_called()

    def test_an_executed_order_on_the_expiry_date_guards_the_candidate(self, upserts):
        db = _make_db(
            [
                _journal_row(
                    "SELL_TO_OPEN", 10, ticker="TSLA", strike=400.0, right="P",
                    date_str=(EXPIRED - timedelta(days=7)).strftime("%Y-%m-%d"),
                    ib_exec_id="0001eb2e.69fdf46c.01.01",
                ),
            ],
            [
                _executed_row(
                    ticker="TSLA", strike=400.0, right="P",
                    fill_time=f"{EXPIRED.strftime('%Y-%m-%d')}T14:00:00Z",
                ),
            ],
        )

        result = _make_handler(db).execute()

        assert result["closed"] == 0
        upserts.assert_not_called()
